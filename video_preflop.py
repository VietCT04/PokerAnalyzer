"""Offline, layout-based OCR for the preflop portion of a poker recording."""

import argparse
import csv
import io
import json
import os
import re
import subprocess
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from imageio_ffmpeg import get_ffmpeg_exe


PROFILE = Path(__file__).with_name("examples") / "rush-video-layout.json"
RANKS = "23456789TJQKA"
AMOUNT = r"[0-9]{1,4}(?:[.,][0-9])?"
BB = r"(?:BB|8B|B8|BE|8E)"
FIELD_COLUMNS = (
    "snapshot_id", "table", "video_seconds", "visual_seat", "nickname_candidate", "nickname_confidence",
    "visible_stack_bb", "starting_stack_bb", "posted_blind_bb", "recorded_player_cards",
    "starting_stack_method", "dealer_visual_seat", "pot_bb", "nickname_ocr", "stack_ocr", "blind_ocr",
)


def region(frame, box, offset):
    x1, y1, x2, y2 = box
    x, y = offset
    return frame[y+y1:y+y2, x+x1:x+x2]


def white_pixels(crop):
    return (crop.min(axis=2) >= 145) & (crop.max(axis=2) - crop.min(axis=2) < 80)


def card_color(crop):
    red, green, blue = crop.astype(np.int16).reshape(-1, 3).T
    votes = (
        ((red - green > 92) & (red - blue > 85)).sum(),
        ((blue > 108) & (blue - red > 28) & (blue - green > 15)).sum(),
        ((green > 97) & (green - red > 28) & (green - blue > 25)).sum(),
        ((red < 78) & (green < 78) & (blue < 78)).sum(),
    )
    winner = max(range(4), key=votes.__getitem__)
    return "hdcs"[winner] if votes[winner] >= len(red) * 0.50 else ""


def pockets(frame, layout, offset):
    masks, suits = [], []
    if white_pixels(region(frame, layout["board"], offset)).sum() > 105:
        return None
    for card in layout["cards"]:
        mask = white_pixels(region(frame, card["rank"], offset))
        suit = card_color(region(frame, card["color"], offset))
        symbol = white_pixels(region(frame, card["suit"], offset))
        if not suit or not 13 <= mask.sum() <= 110 or not 25 <= symbol.sum() <= 130:
            return None
        masks.append(mask)
        suits.append(suit)
    return "".join(suits), masks


def same_pocket(left, right):
    if left[0] != right[0]:
        return False
    return all(
        np.count_nonzero(a != b) <= max(14, int(np.count_nonzero(a | b) * 0.32))
        for a, b in zip(left[1], right[1])
    )


def dealer_seat(frame, layout, offset):
    seats = []
    for seat, box in layout["dealer"].items():
        red, green, blue = region(frame, box, offset).astype(np.int16).transpose(2, 0, 1)
        gold = (red >= 155) & (green >= 120) & (blue <= 110) & (green - blue >= 55) & (red > green)
        if 40 <= gold.sum() <= 115:
            ys, xs = np.nonzero(gold)
            if 7 <= np.ptp(xs) <= 14 and 7 <= np.ptp(ys) <= 14:
                seats.append(seat)
    return seats[0] if len(seats) == 1 else ""


@dataclass
class FrameSample:
    seconds: float
    table: str
    suits: str
    dealer: str
    crops: dict
    review_jpeg: bytes


def snapshot(frame, seconds, table, offset, suits, dealer, layout, review):
    crops = {"pot": region(frame, layout["pot"], offset).copy()}
    for index, card in enumerate(layout["cards"]):
        crops[f"rank{index}"] = region(frame, card["rank"], offset).copy()
    for seat, boxes in layout["seats"].items():
        for field, box in boxes.items():
            crops[f"{seat}.{field}"] = region(frame, box, offset).copy()
    photo = io.BytesIO()
    if review:
        Image.fromarray(region(frame, layout["review_crop"], offset)).save(photo, format="JPEG", quality=94)
    return FrameSample(seconds, table, suits, dealer, crops, photo.getvalue())


def scan_frames(video, layout, start, fps, review):
    width, height = layout["size"]
    size = width * height * 3
    cmd = [
        get_ffmpeg_exe(), "-hide_banner", "-loglevel", "info", "-benchmark", "-nostdin", "-threads", "4",
        "-ss", str(start), "-i", str(video), "-map", "0:v:0", "-an", "-sn", "-dn",
        "-vf", f"fps={fps}", "-threads:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    captures = []
    groups = {}
    frames = 0
    with tempfile.TemporaryFile() as log, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log) as decoder:
        while raw := decoder.stdout.read(size):
            if len(raw) != size:
                raise ValueError("The video ended during a frame")
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            seconds = start + frames / fps
            frames += 1
            for table, offset in layout["tables"].items():
                signature = pockets(frame, layout, offset)
                if not signature:
                    continue
                dealer = dealer_seat(frame, layout, offset)
                previous = groups.get(table)
                if previous:
                    previous_signature, last_seen, samples = previous
                    first = samples[0]
                    same_dealer = dealer and dealer == first.dealer
                    close = seconds - first.seconds <= (1.25 if same_dealer else 0.25)
                    similar = seconds - last_seen <= (6 if same_dealer else 1.25) and same_pocket(
                        previous_signature, signature
                    )
                    if close or similar:
                        groups[table] = (previous_signature, seconds, samples)
                        if len(samples) < 4 and seconds - first.seconds <= 1.5:
                            samples.append(snapshot(frame, seconds, table, offset, signature[0], dealer, layout, review))
                        continue
                samples = [snapshot(frame, seconds, table, offset, signature[0], dealer, layout, review)]
                groups[table] = (signature, seconds, samples)
                captures.append(samples)
        decoder.wait()
        log.seek(0)
        details = log.read().decode("utf-8", errors="replace")
    if decoder.returncode:
        raise ValueError(details[-1500:])
    benchmark = re.search(r"utime=([\d.]+)s stime=([\d.]+)s rtime=([\d.]+)s", details)
    cpu = round(float(benchmark[1]) + float(benchmark[2]), 3) if benchmark else None
    return captures, frames, cpu


def stack_number_image(crop):
    red, green, blue = crop.astype(np.int16).transpose(2, 0, 1)
    mask = (blue >= 105) & (green >= 89) & (blue - red >= 22) & (green - red >= 17)
    occupied = np.flatnonzero(mask.sum(axis=0) >= 2)
    runs = np.split(occupied, np.flatnonzero(np.diff(occupied) > 2) + 1)
    runs = [run for run in runs if len(run) >= 2]
    # The two BB glyphs form a separate 7-13-pixel group at the right of the amount.
    if len(runs) < 2 or not 7 <= runs[-1][-1] - runs[-1][0] + 1 <= 13:
        return np.full((12, 15), 255, dtype=np.uint8)
    start, end = max(0, runs[0][0] - 1), runs[-1][0]
    cyan = (blue - red >= 8) & (green - red >= 5)
    light = np.where(cyan, (green - 80) * 2.8, 0)
    return (255 - light[:, start:end].clip(0, 255)).astype(np.uint8)


def crop_image(crop, kind):
    if kind == "rank":
        light = (crop.min(axis=2).astype(np.int16) - 100) * 3
        gray = Image.fromarray((255 - light.clip(0, 255)).astype(np.uint8))
    elif kind == "stack":
        gray = Image.fromarray(stack_number_image(crop))
    else:
        gray = ImageOps.invert(ImageOps.autocontrast(Image.fromarray(crop).convert("L"), cutoff=1))
    resized = gray.resize((gray.width * 4, gray.height * 4), Image.Resampling.LANCZOS)
    return ImageOps.expand(resized, border=12, fill=255)


def ocr_batch(crops, kind, tesseract, folder):
    if not crops:
        return []
    batch = folder / kind
    batch.mkdir()
    inputs = []
    for i, crop in enumerate(crops):
        path = batch / f"{i:05}.png"
        crop_image(crop, kind).save(path, compress_level=1)
        inputs.append(str(path))
    listing = batch / "images.txt"
    listing.write_text("\n".join(inputs) + "\n", encoding="utf-8")
    command = [
        str(tesseract), str(listing), "stdout", "--psm", "10" if kind == "rank" else "7",
        "-c", "load_system_dawg=0", "-c", "load_freq_dawg=0",
    ]
    if kind == "rank":
        command += ["-c", "tessedit_char_whitelist=23456789TJQKA10"]
    elif kind == "stack":
        command += ["-c", "tessedit_char_whitelist=0123456789.,"]
    elif kind != "name":
        command += ["-c", "tessedit_char_whitelist=TotalPOTpot:0123456789.,BBE "]
    result = subprocess.run(command + ["tsv"], capture_output=True, encoding="utf-8", errors="replace",
                            env={**os.environ, "OMP_THREAD_LIMIT": "1"})
    if result.returncode:
        raise ValueError(result.stderr[-1500:])
    pages = defaultdict(list)
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") == "5" and row.get("text", "").strip():
            pages[int(row["page_num"]) - 1].append((row["text"].strip(), float(row["conf"])))
    return [(" ".join(text for text, _ in pages[i]), min((score for _, score in pages[i]), default=0.0))
            for i in range(len(crops))]


def amount(text):
    reading = re.sub(r"^TOTAL\s*POT\s*:?", "", text.upper().strip()).strip(" ,.:")
    match = re.fullmatch(rf"({AMOUNT})\s*(?:{BB})?", reading)
    return Decimal(match[1].replace(",", ".")) if match else None


def nickname(text):
    cleaned = text.strip(" |~_")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{1,20}", cleaned) and not re.match(
        r"^(?:post blind|waiting|fold|raise|call|all.in|sitting out|time out)", cleaned, re.I
    ):
        return cleaned
    return ""


def rank(text):
    value = text.strip().upper()
    if value == "10":
        return "T"
    return value if len(value) == 1 and value in RANKS else ""


def choose_frames(candidates, tesseract, work):
    samples = [sample for group in candidates for sample in group]
    pots = ocr_batch([sample.crops["pot"] for sample in samples], "pot", tesseract, work)
    detected = [(sample, amount(text)) for sample, (text, _) in zip(samples, pots)]
    offset = 0
    selected = []
    for group in candidates:
        matches = [sample for sample, pot in detected[offset:offset+len(group)] if pot == Decimal("1.5")]
        offset += len(group)
        if matches:
            selected.append(matches[0])
    return selected, len(samples)


def starting_blinds(seats, dealer, visible, readings):
    small, big = Decimal("0.5"), Decimal(1)
    posted = {i: value for i, value in enumerate(readings) if value is not None}
    direct = sorted(posted.values()) == [small, big]
    if dealer in seats and len(seats) == 6 and all(value is not None for value in visible):
        button = seats.index(dealer)
        expected = {(button + 1) % 6: small, (button + 2) % 6: big}
        if any(value != expected.get(i) for i, value in posted.items()):
            return {}, ""
        return expected, "visible_blinds" if direct else "dealer_and_blind_only_pot"
    return (posted, "visible_blinds") if direct else ({}, "")


def observations(selected, tesseract, work, layout, review_folder):
    selected = sorted(selected, key=lambda sample: (sample.seconds, sample.table))
    seats = tuple(layout["seats"])
    ranks = iter(ocr_batch([s.crops[f"rank{i}"] for s in selected for i in range(2)], "rank", tesseract, work))
    fields = {}
    for field in ("name", "stack", "blind"):
        fields[field] = iter(ocr_batch([s.crops[f"{seat}.{field}"] for s in selected for seat in seats],
                                       field, tesseract, work))
    for snapshot_id, sample in enumerate(selected, 1):
        first, second = rank(next(ranks)[0]), rank(next(ranks)[0])
        card1, card2 = first + sample.suits[0], second + sample.suits[1]
        cards = f"{card1} {card2}" if first and second and card1 != card2 else ""
        readings = [{k: next(stream) for k, stream in fields.items()} for _ in seats]
        stacks = [amount(row["stack"][0]) for row in readings]
        posted, method = starting_blinds(seats, sample.dealer, stacks, [amount(row["blind"][0]) for row in readings])
        if review_folder:
            (review_folder / f"{snapshot_id:04}.jpg").write_bytes(sample.review_jpeg)
        for i, (seat, row, visible) in enumerate(zip(seats, readings, stacks)):
            starting = visible + posted.get(i, Decimal(0)) if visible is not None and method else None
            yield {
                "snapshot_id": snapshot_id, "table": sample.table, "video_seconds": round(sample.seconds, 3),
                "visual_seat": seat, "nickname_candidate": nickname(row["name"][0]),
                "nickname_confidence": round(row["name"][1], 1), "visible_stack_bb": visible,
                "starting_stack_bb": starting, "posted_blind_bb": posted.get(i),
                "starting_stack_method": method if starting is not None else "",
                "dealer_visual_seat": sample.dealer,
                "recorded_player_cards": cards if seat == "bottom" else "", "pot_bb": "1.5",
                "nickname_ocr": row["name"][0], "stack_ocr": row["stack"][0], "blind_ocr": row["blind"][0],
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--layout", type=Path, default=PROFILE)
    parser.add_argument("--tesseract", type=Path, default=Path("tesseract"))
    parser.add_argument("--output", type=Path, required=True, help="new folder for CSV and benchmark JSON")
    parser.add_argument("--start", type=float, default=0, help="start sampling at this video timestamp in seconds")
    parser.add_argument("--fps", type=float, default=4)
    parser.add_argument("--review-frames", action="store_true", help="save table crops by snapshot ID in the output folder")
    args = parser.parse_args()
    try:
        if args.start < 0 or not 0 < args.fps <= 30:
            raise ValueError("start must be nonnegative and fps must be between 0 and 30")
        layout = json.loads(args.layout.read_text(encoding="utf-8"))
        args.output.mkdir(parents=True, exist_ok=False)
        review_folder = args.output / "frames" if args.review_frames else None
        if review_folder:
            review_folder.mkdir()
        started, cpu = time.perf_counter(), time.process_time()
        groups, frames, decoder_cpu = scan_frames(args.video, layout, args.start, args.fps, args.review_frames)
        scanned = time.perf_counter()
        with tempfile.TemporaryDirectory(dir=args.output.resolve()) as tmp:
            selected, screened = choose_frames(groups, args.tesseract, Path(tmp))
            rows = list(observations(selected, args.tesseract, Path(tmp), layout, review_folder))
        finished = time.perf_counter()
        with (args.output / "preflop.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELD_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        summary = {
            "source": args.video.name, "sample_rate": args.fps, "video_seconds_examined": round(frames / args.fps, 2),
            "sampled_frames": frames, "candidate_hands": len(groups), "candidate_frames_screened": screened,
            "blind_only_snapshots": len(selected), "seat_rows": len(rows),
            "nickname_candidates": sum(bool(row["nickname_candidate"]) for row in rows),
            "visible_stacks_read": sum(row["visible_stack_bb"] is not None for row in rows),
            "starting_stacks_estimated": sum(row["starting_stack_bb"] is not None for row in rows),
            "hole_card_pairs_read": sum(bool(row["recorded_player_cards"]) for row in rows),
            "scan_seconds": round(scanned - started, 3), "ocr_seconds": round(finished - scanned, 3),
            "elapsed_seconds": round(finished - started, 3), "ffmpeg_cpu_seconds": decoder_cpu,
            "python_cpu_seconds": round(time.process_time() - cpu, 3),
        }
        (args.output / "benchmark.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()

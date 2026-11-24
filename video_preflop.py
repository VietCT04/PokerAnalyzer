"""Offline, layout-based OCR for the preflop portion of a poker recording."""

import argparse
import csv
import io
import json
import math
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

from video_report import write_report


PROFILE = Path(__file__).with_name("examples") / "rush-video-layout.json"
RANKS = "23456789TJQKA"
AMOUNT = r"[0-9]{1,4}(?:[.,][0-9])?"
BB = r"(?:BB|8B|B8|BE|8E)"
FIELD_COLUMNS = (
    "snapshot_id", "table", "video_seconds", "visual_seat", "nickname_candidate", "nickname_confidence",
    "nickname_video_seconds", "nickname_support", "nickname_status",
    "visible_stack_bb", "starting_stack_bb", "posted_blind_bb", "recorded_player_cards",
    "starting_stack_method", "dealer_visual_seat", "pot_bb", "nickname_ocr", "stack_ocr", "stack_ocr_status", "blind_ocr",
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
    signature: tuple
    dealer: str
    crops: dict
    review_jpeg: bytes


@dataclass
class HandSamples:
    anchors: list
    name_frames: list


def name_ink(crop, is_bottom):
    red, green, blue = crop.astype(np.int16).transpose(2, 0, 1)
    if is_bottom:
        return (red - green >= 22) & (green - blue >= 17) & (red >= 135)
    neutral = np.maximum(red, blue) - np.minimum(red, blue) < 40
    white = neutral & (crop.min(axis=2) >= 115)
    purple = (blue >= 125) & (blue - green >= 35) & (red - green >= 15)
    return white | purple


def has_name_text(crop, is_bottom):
    rows = name_ink(crop, is_bottom).sum(axis=1) >= 3
    return bool(np.any(rows[:-3] & rows[1:-2] & rows[2:-1] & rows[3:]))


def wider_name_crop(frame, box, offset):
    x1, y1, x2, y2 = box
    return region(frame, (max(0, x1 - 6), y1, x2 + 6, y2 + 1), offset).copy()


def name_bounds(crop, is_bottom):
    ink = name_ink(crop, is_bottom)
    columns = ink.shape[1]
    center = ink[:, 9:-9] if columns > 18 else ink
    rows = np.flatnonzero(center.sum(axis=1) >= 3)
    if not rows.size:
        return None
    runs = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
    line = max(runs, key=lambda run: (run.size, center[run].sum()))
    if line.size < 4:
        return None
    top, bottom = line[0], line[-1] + 1
    occupied = np.flatnonzero(ink[top:bottom].sum(axis=0) >= 2)
    occupied = occupied[(occupied >= 5) & (occupied < columns - 5)]
    if not occupied.size:
        return None
    left, right = occupied[0], occupied[-1] + 1
    return crop[max(0, top - 2):bottom + 2, left - 2:right + 2]


def name_frame(frame, seconds, signature, dealer, offset, layout):
    crops = [(region(frame, boxes["name"], offset).copy(), wider_name_crop(frame, boxes["name"], offset))
             for boxes in layout["seats"].values()]
    return seconds, signature, dealer, crops


def snapshot(frame, seconds, table, offset, signature, dealer, layout, review, enhance_names):
    crops = {"pot": region(frame, layout["pot"], offset).copy()}
    for index, card in enumerate(layout["cards"]):
        crops[f"rank{index}"] = region(frame, card["rank"], offset).copy()
    for seat, boxes in layout["seats"].items():
        for field, box in boxes.items():
            crops[f"{seat}.{field}"] = region(frame, box, offset).copy()
        if enhance_names:
            crops[f"{seat}.name_search"] = wider_name_crop(frame, boxes["name"], offset)
    photo = io.BytesIO()
    if review:
        Image.fromarray(region(frame, layout["review_crop"], offset)).save(photo, format="JPEG", quality=94)
    return FrameSample(seconds, table, signature, dealer, crops, photo.getvalue())


def scan_frames(video, layout, start, fps, review, enhance_names):
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
                    first = samples.anchors[0]
                    same_dealer = dealer and dealer == first.dealer
                    close = seconds - first.seconds <= (1.25 if same_dealer else 0.25)
                    similar = seconds - last_seen <= (6 if same_dealer else 1.25) and same_pocket(
                        previous_signature, signature
                    )
                    if close or similar:
                        groups[table] = (previous_signature, seconds, samples)
                        if len(samples.anchors) < 4 and seconds - first.seconds <= 1.5:
                            samples.anchors.append(snapshot(
                                frame, seconds, table, offset, signature, dealer, layout, review, enhance_names
                            ))
                        last_name = samples.name_frames[-1][0] if samples.name_frames else first.seconds
                        if (enhance_names and seconds - first.seconds <= 3 and seconds - last_seen <= .75
                                and seconds - last_name >= .5
                                and (same_dealer or not dealer or not first.dealer)
                                and same_pocket(previous_signature, signature)):
                            samples.name_frames.append(name_frame(frame, seconds, signature, dealer, offset, layout))
                        continue
                anchor = snapshot(frame, seconds, table, offset, signature, dealer, layout, review, enhance_names)
                samples = HandSamples([anchor], [])
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


def select_nickname(readings):
    by_label = defaultdict(dict)
    for seconds, text, score, crop in readings:
        label = nickname(text)
        if not label or score < 65:
            continue
        votes = by_label[label.casefold()]
        if seconds not in votes or score > votes[seconds][1]:
            votes[seconds] = (label, score, text, crop)
    choices = []
    for votes in by_label.values():
        seconds, (label, score, text, crop) = max(votes.items(), key=lambda item: item[1][1])
        choices.append((len(votes), score, seconds, label, text, crop))
    choices.sort(key=lambda choice: choice[:2], reverse=True)
    if choices:
        support, score, seconds, label, text, crop = choices[0]
        rival = choices[1] if len(choices) > 1 else None
        if rival and ((support == rival[0] and rival[1] >= score - 12) or (rival[0] >= 2 and rival[1] >= 65)):
            return "", None, None, 0, "disagrees", text, None
        if (support >= 2 and score >= 75) or score >= 88:
            status = "repeated_ocr" if support >= 2 else "single_frame_ocr"
            return label, round(score, 1), seconds, support, status, text, crop
    raw = max(readings, key=lambda reading: reading[2])[1] if readings else ""
    return "", None, None, 0, "unreadable", raw, None


def open_nickname_model(model):
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise ValueError("Install onnxruntime to use --nickname-model") from exc
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    try:
        session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
    except Exception as exc:
        raise ValueError(f"Cannot load nickname model: {exc}") from exc
    characters = session.get_modelmeta().custom_metadata_map.get("character", "").splitlines()
    alphabet = ["", *characters, " "]
    if not characters or session.get_outputs()[0].shape[-1] != len(alphabet):
        raise ValueError("Use RapidOCR's English PP-OCRv5 mobile recognition ONNX model")
    return session, alphabet


def recognize_labels(crops, session, alphabet):
    input_name = session.get_inputs()[0].name
    height, batch_size = 48, 16
    results = []
    for start in range(0, len(crops), batch_size):
        images = crops[start:start + batch_size]
        widths = [min(384, math.ceil(height * image.shape[1] / image.shape[0])) for image in images]
        width = 32 * math.ceil(max(widths) / 32)
        batch = np.zeros((len(images), 3, height, width), dtype=np.float32)
        for row, pixels, resized_width in zip(batch, images, widths):
            image = Image.fromarray(pixels).resize((resized_width, height), Image.Resampling.BILINEAR)
            row[:, :, :resized_width] = np.asarray(image, dtype=np.float32).transpose(2, 0, 1) / 127.5 - 1
        probabilities = session.run(None, {input_name: batch})[0]
        for sequence, scores in zip(probabilities.argmax(axis=-1), probabilities.max(axis=-1)):
            keep = sequence != 0
            keep[1:] &= sequence[1:] != sequence[:-1]
            results.append(("".join(alphabet[index] for index in sequence[keep]),
                            100 * float(scores[keep].min()) if keep.any() else 0.0))
    return results


def frame_nickname_readings(candidates, session, alphabet):
    pages = {}
    assignments = []
    for row in candidates:
        readings = []
        for seconds, pixels in row:
            key = pixels.shape, pixels.tobytes()
            if key not in pages:
                pages[key] = len(pages), pixels
            readings.append((seconds, pages[key][0], pixels))
        assignments.append(readings)
    ocr = recognize_labels([pixels for _, pixels in pages.values()], session, alphabet)
    return [[(seconds, *ocr[page], pixels) for seconds, page, pixels in readings] for readings in assignments]


def agree_on_wider_label(readings):
    corroborated = []
    for wider, bounded in zip(readings[::2], readings[1::2]):
        seconds, text, score, crop = max((wider, bounded), key=lambda reading: reading[2])
        if (nickname(wider[1]).casefold() == nickname(bounded[1]).casefold()
                and min(wider[2], bounded[2]) >= 64):
            corroborated.append((seconds, text, score, crop))
    result = select_nickname(corroborated)
    if result[0]:
        return (*result[:4], "wide_label_ocr", *result[5:])
    return result


def read_names(selected, tesseract, work, seats, model):
    crops = [sample.crops[f"{seat}.name"] for sample, _ in selected for seat in seats]
    if model is None:
        times = [sample.seconds for sample, _ in selected for _ in seats]
        ocr = ocr_batch(crops, "name", tesseract, work)
        return [select_nickname([(seconds, text, score, crop)])
                for seconds, (text, score), crop in zip(times, ocr, crops)]
    session, alphabet = model
    anchors = []
    for (sample, _), crop_start in zip(selected, range(0, len(crops), len(seats))):
        for seat, pixels in zip(seats, crops[crop_start:crop_start + len(seats)]):
            anchors.append([(sample.seconds, pixels)] if has_name_text(pixels, seat == "bottom") else [])
    first_readings = frame_nickname_readings(anchors, session, alphabet)
    later_candidates = []
    wider_candidates = []
    for sample, frames in selected:
        later = [(seconds, crops) for seconds, signature, dealer, crops in frames
                 if 0 < seconds - sample.seconds <= 3 and same_pocket(sample.signature, signature)
                 and (not sample.dealer or not dealer or sample.dealer == dealer)]
        for seat_index, seat in enumerate(seats):
            first = first_readings[len(later_candidates)]
            if first and first[0][2] >= 93 and nickname(first[0][1]):
                later_candidates.append([])
                wider_candidates.append([])
                continue
            clean = [(seconds, crops[seat_index]) for seconds, crops in later
                     if has_name_text(crops[seat_index][0], seat == "bottom")][:3]
            later_candidates.append([(seconds, ordinary) for seconds, (ordinary, _) in clean])
            search = [(sample.seconds, sample.crops[f"{seat}.name_search"])]
            search.extend((seconds, wide) for seconds, (_, wide) in clean)
            wider_candidates.append((seat == "bottom", search))
    later_readings = frame_nickname_readings(later_candidates, session, alphabet)
    results = [select_nickname(first + later) for first, later in zip(first_readings, later_readings)]
    unresolved = {}
    for i, (result, candidates) in enumerate(zip(results, wider_candidates)):
        if result[4] != "unreadable" or not candidates:
            continue
        is_bottom, search = candidates
        variants = []
        for seconds, wide in search:
            bounded = name_bounds(wide, is_bottom)
            if bounded is not None:
                variants.extend(((seconds, wide), (seconds, bounded)))
        if variants:
            unresolved[i] = variants
    extra = frame_nickname_readings(list(unresolved.values()), session, alphabet)
    for i, readings in zip(unresolved, extra):
        fallback = agree_on_wider_label(readings)
        if fallback[0]:
            results[i] = fallback
    return results


def rank(text):
    value = text.strip().upper()
    if value == "10":
        return "T"
    return value if len(value) == 1 and value in RANKS else ""


def choose_frames(candidates, tesseract, work):
    samples = [sample for group in candidates for sample in group.anchors]
    pots = ocr_batch([sample.crops["pot"] for sample in samples], "pot", tesseract, work)
    detected = [(sample, amount(text)) for sample, (text, _) in zip(samples, pots)]
    offset = 0
    selected = []
    for group in candidates:
        matches = [sample for sample, pot in detected[offset:offset+len(group.anchors)] if pot == Decimal("1.5")]
        offset += len(group.anchors)
        if matches:
            selected.append((matches[0], group.name_frames))
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


def check_four_digit_stack(text, readings):
    # A missing decimal point can turn 104.6 BB into 1046 BB. Check the unmasked label twice.
    values = [amount(label) for label, score in readings if score >= 65 and label.rstrip().upper().endswith("BB")]
    if len(values) != 2 or values[0] != values[1]:
        return None
    original = Decimal(text)
    return values[0] if values[0] in (original, original / 10) else None


def read_stacks(crops, tesseract, work, model):
    ocr = ocr_batch(crops, "stack", tesseract, work)
    results = [(amount(text), text, "") for text, _ in ocr]
    flagged = [i for i, (_, text, _) in enumerate(results) if re.fullmatch(r"[1-9][0-9]{3}", text)]
    if not flagged:
        return results
    raw_crops = [variant for i in flagged for variant in (crops[i], crops[i][1:])]
    checked = recognize_labels(raw_crops, *model) if model is not None else []
    for pair_index, i in enumerate(flagged):
        _, text, _ = results[i]
        value = check_four_digit_stack(text, checked[pair_index * 2:pair_index * 2 + 2])
        results[i] = value, text, "review" if value is None else "checked"
    return results


def observations(selected, tesseract, work, layout, review_folder, model):
    selected = sorted(selected, key=lambda pair: (pair[0].seconds, pair[0].table))
    seats = tuple(layout["seats"])
    ranks = iter(ocr_batch([s.crops[f"rank{i}"] for s, _ in selected for i in range(2)], "rank", tesseract, work))
    names = iter(read_names(selected, tesseract, work, seats, model))
    stacks = iter(read_stacks([s.crops[f"{seat}.stack"] for s, _ in selected for seat in seats], tesseract, work, model))
    blinds = iter(ocr_batch([s.crops[f"{seat}.blind"] for s, _ in selected for seat in seats],
                            "blind", tesseract, work))
    for snapshot_id, (sample, _) in enumerate(selected, 1):
        first, second = rank(next(ranks)[0]), rank(next(ranks)[0])
        suits = sample.signature[0]
        card1, card2 = first + suits[0], second + suits[1]
        cards = f"{card1} {card2}" if first and second and card1 != card2 else ""
        readings = [next(stacks) for _ in seats]
        blind_texts = [next(blinds)[0] for _ in seats]
        posted, method = starting_blinds(
            seats, sample.dealer, [value for value, _, _ in readings], [amount(text) for text in blind_texts]
        )
        if review_folder:
            (review_folder / f"{snapshot_id:04}.jpg").write_bytes(sample.review_jpeg)
        for i, (seat, (visible, stack_text, stack_status), blind_text) in enumerate(zip(seats, readings, blind_texts)):
            starting = visible + posted.get(i, Decimal(0)) if visible is not None and method else None
            name, confidence, name_seconds, support, status, raw_name, name_crop = next(names)
            if review_folder:
                path = review_folder / f"{snapshot_id:04}-{seat}.png"
                image = Image.fromarray(name_crop if name_crop is not None else sample.crops[f"{seat}.name"])
                image.resize((image.width * 4, image.height * 4), Image.Resampling.NEAREST).save(path)
            yield {
                "snapshot_id": snapshot_id, "table": sample.table, "video_seconds": round(sample.seconds, 3),
                "visual_seat": seat, "nickname_candidate": name, "nickname_confidence": confidence,
                "nickname_video_seconds": round(name_seconds, 3) if name_seconds is not None else None,
                "nickname_support": support, "nickname_status": status, "visible_stack_bb": visible,
                "starting_stack_bb": starting, "posted_blind_bb": posted.get(i),
                "starting_stack_method": method if starting is not None else "",
                "dealer_visual_seat": sample.dealer,
                "recorded_player_cards": cards if seat == "bottom" else "", "pot_bb": "1.5",
                "nickname_ocr": raw_name, "stack_ocr": stack_text, "stack_ocr_status": stack_status,
                "blind_ocr": blind_text,
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--layout", type=Path, default=PROFILE)
    parser.add_argument("--tesseract", type=Path, default=Path("tesseract"))
    parser.add_argument("--nickname-model", type=Path, help="English PP-OCRv5 mobile recognition ONNX model")
    parser.add_argument("--output", type=Path, required=True, help="new folder for CSV and benchmark JSON")
    parser.add_argument("--start", type=float, default=0, help="start sampling at this video timestamp in seconds")
    parser.add_argument("--fps", type=float, default=4)
    parser.add_argument("--review-frames", action="store_true", help="save table crops by snapshot ID in the output folder")
    args = parser.parse_args()
    try:
        if args.start < 0 or not 0 < args.fps <= 30:
            raise ValueError("start must be nonnegative and fps must be between 0 and 30")
        if args.nickname_model is not None and not args.nickname_model.is_file():
            raise ValueError("The nickname model file does not exist")
        layout = json.loads(args.layout.read_text(encoding="utf-8"))
        args.output.mkdir(parents=True, exist_ok=False)
        review_folder = args.output / "frames" if args.review_frames else None
        if review_folder:
            review_folder.mkdir()
        started, cpu = time.perf_counter(), time.process_time()
        nickname_model = open_nickname_model(args.nickname_model) if args.nickname_model is not None else None
        groups, frames, decoder_cpu = scan_frames(
            args.video, layout, args.start, args.fps, args.review_frames, args.nickname_model is not None
        )
        scanned = time.perf_counter()
        with tempfile.TemporaryDirectory(dir=args.output.resolve()) as tmp:
            selected, screened = choose_frames(groups, args.tesseract, Path(tmp))
            rows = list(observations(selected, args.tesseract, Path(tmp), layout, review_folder, nickname_model))
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
            "nickname_backend": "ppocrv5_mobile" if args.nickname_model is not None else "tesseract",
            "repeated_nickname_reads": sum(row["nickname_status"] == "repeated_ocr" for row in rows),
            "wider_label_reads": sum(row["nickname_status"] == "wide_label_ocr" for row in rows),
            "visible_stacks_read": sum(row["visible_stack_bb"] is not None for row in rows),
            "four_digit_stacks_checked": sum(row["stack_ocr_status"] == "checked" for row in rows),
            "starting_stacks_estimated": sum(row["starting_stack_bb"] is not None for row in rows),
            "hole_card_pairs_read": sum(bool(row["recorded_player_cards"]) for row in rows),
            "scan_seconds": round(scanned - started, 3), "ocr_seconds": round(finished - scanned, 3),
            "elapsed_seconds": round(finished - started, 3), "ffmpeg_cpu_seconds": decoder_cpu,
            "python_cpu_seconds": round(time.process_time() - cpu, 3),
        }
        (args.output / "benchmark.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        write_report(args.output, rows, summary)
        print(json.dumps(summary, indent=2))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()

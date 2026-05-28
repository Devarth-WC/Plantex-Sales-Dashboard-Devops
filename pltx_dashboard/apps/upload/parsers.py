import datetime
import os
import re

import pandas as pd


try:
    CSV_CHUNK_SIZE = max(int(os.getenv("UPLOAD_CSV_CHUNK_SIZE", "50000")), 5000)
except (TypeError, ValueError):
    CSV_CHUNK_SIZE = 50_000


def load_file_obj(file_obj, **kwargs):
    filename = getattr(file_obj, "name", "").lower()
    if filename.endswith(".csv"):
        try:
            return pd.read_csv(file_obj, **kwargs)
        except UnicodeDecodeError:
            file_obj.seek(0)
            return pd.read_csv(file_obj, encoding="latin1", **kwargs)
    try:
        return pd.read_excel(file_obj, **kwargs)
    except Exception:
        file_obj.seek(0)
        return pd.read_excel(file_obj, engine="openpyxl", **kwargs)


def iter_file_chunks(file_obj, **kwargs):
    filename = getattr(file_obj, "name", "").lower()
    if filename.endswith(".csv"):
        read_kwargs = dict(kwargs)
        read_kwargs["chunksize"] = read_kwargs.get("chunksize", CSV_CHUNK_SIZE)
        try:
            yield from pd.read_csv(file_obj, **read_kwargs)
        except UnicodeDecodeError:
            file_obj.seek(0)
            yield from pd.read_csv(file_obj, encoding="latin1", **read_kwargs)
    else:
        yield load_file_obj(file_obj, **kwargs)


def extract_fk_report_date_from_metadata(file_obj):
    report_date = None
    original_pos = file_obj.tell()
    try:
        file_obj.seek(0)
        header_lines = []
        for _ in range(2):
            line = file_obj.readline()
            if not line:
                break
            if isinstance(line, bytes):
                line = line.decode("utf-8-sig", errors="ignore")
            header_lines.append(line.strip())

        for raw_line in header_lines:
            if "," not in raw_line:
                continue
            key, val = raw_line.split(",", 1)
            if key.strip().lower() in {"start time", "end time"}:
                dt = pd.to_datetime(val.strip(), errors="coerce")
                if not pd.isna(dt):
                    report_date = dt.date()
                    break
    finally:
        file_obj.seek(original_pos)
    return report_date


def parse_numeric_report_date(value):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None

    if pd.isna(num) or num <= 0:
        return None

    as_int = int(round(num))

    if abs(num - as_int) < 1e-9 and 19000101 <= as_int <= 21001231:
        dt = pd.to_datetime(str(as_int), format="%Y%m%d", errors="coerce")
        if not pd.isna(dt):
            return dt.date()

    if 10_000 <= num <= 90_000:
        dt = pd.to_datetime(num, unit="D", origin="1899-12-30", errors="coerce")
        if not pd.isna(dt):
            return dt.date()

    if num >= 1_000_000_000_000:
        dt = pd.to_datetime(num, unit="ms", origin="unix", errors="coerce")
        if not pd.isna(dt):
            return dt.date()
    elif num >= 1_000_000_000:
        dt = pd.to_datetime(num, unit="s", origin="unix", errors="coerce")
        if not pd.isna(dt):
            return dt.date()

    return None


def parse_report_date(value, prefer_dayfirst=None):
    if pd.isna(value):
        raise ValueError("empty date")

    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = parse_numeric_report_date(value)
        if parsed:
            return parsed
        raise ValueError(f"unsupported numeric date: {value}")

    raw = str(value).strip()
    if not raw:
        raise ValueError("empty date")

    parsed = parse_numeric_report_date(raw.replace(",", ""))
    if parsed:
        return parsed

    if re.fullmatch(r"\d{1,2}[-/]\d{1,2}[-/]\d{4}", raw):
        if prefer_dayfirst is True:
            fmts = ("%d-%m-%Y", "%d/%m/%Y", "%m-%d-%Y", "%m/%d/%Y")
        elif prefer_dayfirst is False:
            fmts = ("%m-%d-%Y", "%m/%d/%Y", "%d-%m-%Y", "%d/%m/%Y")
        else:
            fmts = ("%m-%d-%Y", "%m/%d/%Y", "%d-%m-%Y", "%d/%m/%Y")
        for fmt in fmts:
            try:
                return datetime.datetime.strptime(raw, fmt).date()
            except ValueError:
                pass

    dt = pd.to_datetime(raw, errors="coerce", dayfirst=False)
    if pd.isna(dt):
        dt = pd.to_datetime(raw, errors="coerce", dayfirst=True)
    if pd.isna(dt):
        raise ValueError(f"unparseable date: {value}")
    return dt.date()

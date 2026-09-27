# PictureDateName

PictureDateName is a desktop GUI for renaming photos and videos using dates found in metadata, filenames, or filesystem timestamps. It provides a live preview before renaming and can undo the most recent completed batch.

This README describes `photo_renamer_v3.py`.

## Features

- Choose between **Earliest date** and **First found** selection modes.
- Select and reorder date sources.
- Read photo dates from EXIF (`DateTimeOriginal`, `DateTimeDigitized`, or `DateTime`).
- Read camera capture dates embedded in iPhone and other QuickTime-style video metadata.
- Read QuickTime media creation dates from MP4/MOV-family files.
- Detect dates in filenames, including:
  - `20251026_143012`
  - `2025-10-26 14.30.12`
  - `20251026`
  - `2025-10-26`
- Use file modified or created time as optional fallback sources.
- Add individual files or scan folders, optionally including subfolders.
- Set separate photo and video prefixes and choose a Python `strftime` date format.
- Show the selected date and source in the preview.
- Avoid overwriting files by adding numbered suffixes such as `_1`.
- Undo the most recent completed rename batch.
- Switch between dark and light themes.

## Supported formats

### Images

`.jpg`, `.jpeg`, `.png`, `.bmp`, `.tiff`, `.tif`, `.gif`, `.heic`, `.webp`

### Videos

`.mp4`, `.m4v`, `.mov`, `.3gp`, `.mpg`, `.mpeg`, `.avi`, `.mkv`, `.wmv`, `.flv`, `.webm`, `.mts`, `.m2ts`

All listed video formats can be selected and renamed. Embedded video metadata extraction is implemented for MP4/M4V/MOV/3GP QuickTime-style containers; other video formats can still use filename, modified, or created dates.

## Requirements

- Python 3.7 or newer
- Pillow
- Tkinter, usually included with Python on Windows

Install Pillow with:

```bash
python -m pip install pillow
```

## Usage

Run v3 from this folder:

```bash
python photo_renamer_v3.py
```

1. Select **Add files** or **Add folder**.
2. Enable the date sources you want to use and reorder them if needed.
3. Choose a date selection mode:
   - **Earliest date** selects the earliest checked date between January 1, 2000 and today.
   - **First found** selects the first checked source, from top to bottom, that has a date.
4. Choose photo and video prefixes and a date format.
5. Review the preview table.
6. Select **Rename files** and confirm.
7. Use **Undo last rename** to restore the previous batch when needed.

## Date sources

The default source order is:

1. **Camera capture date**: QuickTime metadata such as `com.apple.quicktime.creationdate`, `udta/date`, and `udta/\\xa9day`.
2. **Photo date taken (EXIF)**: EXIF `DateTimeOriginal`, `DateTimeDigitized`, or `DateTime`.
3. **Video media created**: QuickTime `mvhd` creation time.
4. **Date in filename**.
5. **File modified**: disabled by default.
6. **File created**: disabled by default.

The camera capture and video metadata readers return timezone-aware dates when the file provides a timezone. These are converted to local time before comparison and naming.

Dates before 2000 and dates later than today are ignored in **Earliest date** mode. A date-only filename is not preferred over another source with a real time on the same day.

## Name format

The default names are:

- Photos: `IMG_YYYYMMDD_HHMMSS.ext`
- Videos: `VID_YYYYMMDD_HHMMSS.ext`

The date format uses Python `strftime` directives, for example `%Y` for year, `%m` for month, `%d` for day, `%H` for hour, `%M` for minute, and `%S` for second.

## Notes

- Renaming changes filenames only; file contents are not modified.
- Existing files are never overwritten.
- Files with the same target timestamp receive numbered suffixes.
- Files without a usable checked date are skipped.
- The Windows-specific `pywin32` imports present in v2 are not required by v3.

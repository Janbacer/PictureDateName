# PictureDateName

Rename your image and video files by their original creation date using a simple graphical interface. (PowerRename doesn't use the right date! :)

## Features
- Rename images using EXIF date taken
- Rename videos using original creation date from metadata (or file modification time if unavailable)
- Select individual files or entire folders
- Avoids overwriting files with duplicate names

## Requirements
- Python 3.7+
- Pillow
- hachoir

## Usage
1. Run the program:
   ```
   python main.py
   ```
2. Use the GUI to select files or a folder to rename.

## Notes
- Only supported image/video formats will be renamed.
- Video metadata extraction may not work for all formats; modification time is used as fallback.

import os

from PIL import Image
from PIL.ExifTags import TAGS
from datetime import datetime
import tkinter as tk
from tkinter import filedialog
# For video metadata
from hachoir.parser import createParser
from hachoir.metadata import extractMetadata

def rename_selected_images(file_paths):
    image_exts = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif'}
    video_exts = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.mts', '.m2ts', '.3gp'}
    for file_path in file_paths:
        filename = os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()
        try:
            if ext in image_exts:
                image = Image.open(file_path)
                exif_data = image._getexif()
                image.close()
                if not exif_data:
                    print(f"No EXIF data: {filename}")
                    continue
                date_taken = None
                for tag_id, value in exif_data.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if tag == "DateTimeOriginal":
                        date_taken = value
                        break
                if not date_taken:
                    print(f"No DateTimeOriginal: {filename}")
                    continue
                date_obj = datetime.strptime(date_taken, "%Y:%m:%d %H:%M:%S")
                prefix = "IMG_"
                new_filename = prefix + date_obj.strftime("%Y%m%d_%H%M%S") + ext
            elif ext in video_exts:
                # Try to get original creation date from video metadata
                parser = createParser(file_path)
                date_obj = None
                if parser:
                    try:
                        metadata = extractMetadata(parser)
                        if metadata and metadata.has("creation_date"):
                            date_obj = metadata.get("creation_date")
                        elif metadata and metadata.has("date"):
                            date_obj = metadata.get("date")
                    except Exception as e:
                        print(f"Metadata error for {filename}: {e}")
                    finally:
                        parser.stream.close()  # Ensure file is closed
                if not date_obj:
                    # Fallback to file's modification time
                    mtime = os.path.getmtime(file_path)
                    date_obj = datetime.fromtimestamp(mtime)
                prefix = "VID_"
                new_filename = prefix + date_obj.strftime("%Y%m%d_%H%M%S") + ext
            else:
                print(f"Unsupported file type: {filename}")
                continue
            new_path = os.path.join(os.path.dirname(file_path), new_filename)
            # Avoid overwriting if duplicate name exists
            counter = 1
            while os.path.exists(new_path):
                new_filename = prefix + date_obj.strftime("%Y%m%d_%H%M%S") + f"_{counter}" + ext
                new_path = os.path.join(os.path.dirname(file_path), new_filename)
                counter += 1
            os.rename(file_path, new_path)
            print(f"Renamed: {filename} → {new_filename}")
        except Exception as e:
            print(f"Error processing {filename}: {e}")

# Example usage:
def select_files():
    file_paths = filedialog.askopenfilenames(
        title="Select image/video files to rename",
        filetypes=[
            ("Image/Video files", "*.jpg;*.jpeg;*.png;*.bmp;*.tiff;*.gif;*.mp4;*.mov;*.avi;*.mkv;*.wmv;*.flv;*.webm;*.mts;*.m2ts;*.3gp")
        ]
    )
    if file_paths:
        rename_selected_images(file_paths)
    else:
        print("No files selected.")

def select_folder():
    folder_path = filedialog.askdirectory(title="Select folder containing images/videos")
    if folder_path:
        supported_exts = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif', '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.mts', '.m2ts', '.3gp'}
        file_paths = [
            os.path.join(folder_path, f)
            for f in os.listdir(folder_path)
            if os.path.splitext(f)[1].lower() in supported_exts
        ]
        if file_paths:
            rename_selected_images(file_paths)
        else:
            print("No supported files found in folder.")
    else:
        print("No folder selected.")

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Picture/Video Date Renamer")
    root.geometry("350x120")
    tk.Label(root, text="Rename images/videos by date taken", font=("Arial", 12)).pack(pady=10)
    btn_files = tk.Button(root, text="Select Files", width=15, command=select_files)
    btn_files.pack(pady=5)
    btn_folder = tk.Button(root, text="Select Folder", width=15, command=select_folder)
    btn_folder.pack(pady=5)
    root.mainloop()

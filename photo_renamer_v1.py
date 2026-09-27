import os
from PIL import Image, ExifTags
from datetime import datetime
import tkinter as tk
from tkinter import filedialog
from hachoir.parser import createParser
from hachoir.metadata import extractMetadata

def rename_selected_images(file_paths):
    image_exts = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif'}
    video_exts = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.mts', '.m2ts', '.3gp'}
    for file_path in file_paths:
        filename = os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()
        try:
            date_obj = None
            prefix = None
            if ext in image_exts:
                with Image.open(file_path) as image:
                    exif_data = image._getexif()
                if exif_data:
                    date_taken = next((value for tag_id, value in exif_data.items()
                                       if ExifTags.TAGS.get(tag_id, tag_id) == "DateTimeOriginal"), None)
                    if date_taken:
                        date_obj = datetime.strptime(date_taken, "%Y:%m:%d %H:%M:%S")
                        prefix = "IMG_"
                if not date_obj:
                    print(f"No EXIF date for: {filename}")
                    continue
            elif ext in video_exts:
                parser = createParser(file_path)
                if parser:
                    try:
                        metadata = extractMetadata(parser)
                        if metadata:
                            if metadata.has("creation_date"):
                                date_obj = metadata.get("creation_date")
                            elif metadata.has("date"):
                                date_obj = metadata.get("date")
                    except Exception as e:
                        print(f"Metadata error for {filename}: {e}")
                    finally:
                        parser.stream.close()
                if not date_obj:
                    date_obj = datetime.fromtimestamp(os.path.getmtime(file_path))
                prefix = "VID_"
            else:
                print(f"Unsupported file type: {filename}")
                continue
            new_filename = f"{prefix}{date_obj.strftime('%Y%m%d_%H%M%S')}{ext}"
            new_path = os.path.join(os.path.dirname(file_path), new_filename)
            counter = 1
            while os.path.exists(new_path):
                new_filename = f"{prefix}{date_obj.strftime('%Y%m%d_%H%M%S')}_{counter}{ext}"
                new_path = os.path.join(os.path.dirname(file_path), new_filename)
                counter += 1
            os.rename(file_path, new_path)
            print(f"Renamed: {filename} → {new_filename}")
        except Exception as e:
            print(f"Error processing {filename}: {e}")

# Example usage:
def select_files():
    exts = "*.jpg;*.jpeg;*.png;*.bmp;*.tiff;*.gif;*.mp4;*.mov;*.avi;*.mkv;*.wmv;*.flv;*.webm;*.mts;*.m2ts;*.3gp"
    file_paths = filedialog.askopenfilenames(title="Select files to rename", filetypes=[("Supported files", exts)])
    if file_paths:
        rename_selected_images(file_paths)
    else:
        print("No files selected.")

def select_folder():
    exts = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.gif', '.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.mts', '.m2ts', '.3gp'}
    folder_path = filedialog.askdirectory(title="Select folder containing files")
    if folder_path:
        file_paths = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if os.path.splitext(f)[1].lower() in exts]
        if file_paths:
            rename_selected_images(file_paths)
        else:
            print("No supported files found in folder.")
    else:
        print("No folder selected.")

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Picture/Video Date Renamer")
    tk.Label(root, text="Rename images/videos by date taken", font=("Arial", 12)).pack(pady=10)
    tk.Button(root, text="Select Files", width=15, command=select_files).pack(pady=5)
    tk.Button(root, text="Select Folder", width=15, command=select_folder).pack(pady=5)
    root.mainloop()

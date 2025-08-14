import os
from PIL import Image
from PIL.ExifTags import TAGS
from datetime import datetime
import tkinter as tk
from tkinter import filedialog

def rename_images_in_folder(folder_path):
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        
        # Skip if not a file
        if not os.path.isfile(file_path):
            continue
        
        try:
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
            # Format: IMG_YYYYMMDD_hhmmss
            new_filename = "IMG_" + date_obj.strftime("%Y%m%d_%H%M%S") + os.path.splitext(filename)[1]
            new_path = os.path.join(folder_path, new_filename)
            # Avoid overwriting if duplicate name exists
            counter = 1
            while os.path.exists(new_path):
                new_filename = "IMG_" + date_obj.strftime("%Y%m%d_%H%M%S") + f"_{counter}" + os.path.splitext(filename)[1]
                new_path = os.path.join(folder_path, new_filename)
                counter += 1
            os.rename(file_path, new_path)
            print(f"Renamed: {filename} → {new_filename}")
        
        except Exception as e:
            print(f"Error processing {filename}: {e}")

# Example usage:
if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    folder_selected = filedialog.askdirectory(title="Select folder with images")
    if folder_selected:
        rename_images_in_folder(folder_selected)
    else:
        print("No folder selected.")

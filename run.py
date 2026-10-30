"""Pick a video folder -> upload to myupload -> write links into .txt -> post to the site."""
import myupload
import upload_manual

if __name__ == "__main__":
    folder = myupload.pick_folder()
    myupload.prepare(folder)
    upload_manual.open_chrome()
    upload_manual.process_queue(folder)

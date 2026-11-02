"""Pick a video folder -> upload to myupload -> write links into .txt -> post to the site."""
import myupload

if __name__ == "__main__":
    myupload.main(myupload.pick_folder())

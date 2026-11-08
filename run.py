"""Every model folder in turn -> upload to myupload -> write links into .txt -> post to the site.

python run.py          all folders, one after another
python run.py --pick   choose a single folder
python run.py --watch  all folders, then keep checking for new downloads (the service)
"""
import sys

import myupload

if __name__ == "__main__":
    myupload.main(myupload.pick_folder() if "--pick" in sys.argv else None, watch="--watch" in sys.argv)

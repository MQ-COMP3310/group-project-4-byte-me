# Simple flask riddle game

This codebase implements a simple riddle game in Python and Flask.

The user is required to input their name to start the game, once in, you get asked 10 riddles.
The maximum score per riddle is 3 points.
You get 3 guesses per riddle. As you use up your guesses, 1 point is subtracted from that score so if you get one wrong guess you will get 2 points, two wrong guesses is 1 point and three wrong guesses is gameover.
Once the user makes it to the end of the 10th riddle, their score will be added into the highscore file and if they make the top 10, they will be added to the highscore page.

# Setup

To setup the basic website you will need to have the following installed:

- python3
- pip

Pip is the package manager for Python.  You can install the remaining packages required for this task using pip. You will need to run the following:
To start you should create and activate a virtual environment:

- $ python -m venv env        # use `virtualenv env` for Python2, use `python3 ...` for Python3 on Linux & macOS
- $ source env/bin/activate   # use `env\Scripts\activate` on Windows
- $ pip install -r requirements.txt

# Run the website

You can run the website by typing:

- python run.py

You can now browse to the url http://localhost:8000/ to view the website.

## Issues

There is a slight problem with beginning a game that doesnt always happen. Once the user inputs a Username to play the game then clicks on the 'Play' button, there is a possibility of getting a server error. To get past this, press the back button on the browser and then click 'Play' again. Keep trying that until it works.

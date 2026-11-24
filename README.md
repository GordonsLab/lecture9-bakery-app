# Lecture 9 Bakery App

Final bakery order-tracking app for MGOC50 Lecture 9, plus the basic layout for comparison.

## Run the final build

1. Install Python 3 if needed. No additional packages are required.
2. Open `App2_Structured` and double-click `Start App.cmd` on Windows.
3. Keep the app running and open http://127.0.0.1:8001.

Or run `python App2_Structured/app.py --open` from this repository.
The basic layout starts with `python App1_Basic/app.py --open` at http://127.0.0.1:8000.

## Features

- Import the seven supplied daily message files without duplicates.
- Accept or reject requests; validate changes and cancellations.
- Enforce two days notice and 8 oven hours per pickup day.
- Book one $100 helper per day for four additional hours.
- View daily capacity, committed and picked-up sales, helper costs and activity history.
- View baker/helper schedules and a Gantt chart; reverse the latest successful action with Undo.

Simulation begins November 23, 2026. Baking durations are used as scheduling durations. Assumed shifts are baker 9 AM-5 PM and helper 5-9 AM when extra capacity is needed; decorating, cooling and packing times are not supplied.

## Data and privacy

The CSV and Instagram HTML files are supplied lecture example data. This is an offline exercise, not a live Instagram integration. Each app creates a local `bakery.db` on first run. Databases, credentials, unrelated coursework and temporary files are excluded from this repository.

## Check the app

Run `python test_apps.py`. Tests use disposable databases rather than saved exercise data.
Run `python test_http.py` for local HTTP smoke checks.

See `Design_and_Test_Plan.html` for diagrams and assumptions, `Usability_Review_Page26.html` for the AI usability review, and `COMPARISON.md` for the three-sentence build comparison.

## Save points and restore

The initial commit is named `Final build`. Use GitHub's commit history to inspect save points. For a safe local copy of a prior version, use `git switch -c review-save-point COMMIT_SHA`. To undo a later committed change while preserving history, use `git revert COMMIT_SHA`.

The app's Undo button restores the last app action; Git history restores code versions. Back up your local database separately before changing code versions.

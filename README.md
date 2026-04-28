# Runway — Engineering Manager Task Planner
#### CS50x 2026 Final Project
#### Video Demo: https://youtu.be/eECkP19iwb0

## What is Runway?

First up, I'm an aviation nut, and an Engineering Manager by day. So the name felt an obvious choice. Runway is a Flask-based web app designed to be a task manager for Engineering Managers (EMs). EMs context switch constantly different tasks throughout their day. For example, incident response, architecture reviews, and 1:1s. Each task carries a different urgency and demands a different types of cognitive energy.

Runway allows an EM to register an account, login securely, and manage a personal backlog of tasks. Each task captures title and due date, and then also EM-specific context such what type of work it is, who or what is blocked, and which sprint it belongs to, and its current status, and how much cognitive load it places on the EM. The dashboard displays all tasks sorted by cognitive load (highest first) alongside a summary of totals, blocked count, and average load score which gives the EM a realistic picture of their capacity at a glance.

## Files

`app.py` is the core of the app. It contains all Flask routes and the app logic. This includes the login, logout, and registration routes which handle password hashing via Werkzeug and server side sessions via Flask-Session. The main index route queries all tasks for the logged-in user, computes the dashboard statistics, and passes them to the template. There are routes for adding and editing tasks, a delete route, and a dedicated 

`/status/<id>` JSON endpoint that accepts POST requests from the frontend JavaScript to update a task's status without requiring a full page reload.

`schema.sql` defines the two db tables. The `users` table stores a username and a hashed password. The `tasks` table stores all task fields including the user fk, task type, status, blast radius, sprint, cognitive load score, due date, and notes. Both created and updated timestamps are recorded automatically.

`templates/layout.html` is the base Jinja2 template that all other templates extend. It includes the nav bar, Google Fonts, the CSS stylesheet link, the JS file, and the flash message block so that success and error messages appear consistently across every page.

`templates/index.html` renders the main dashboard. It displays the statistics bar at the top and then a responsive card grid of all tasks. Each card shows the task type badge, cognitive load indicator, blast radius warning if present, sprint label, due date, a live status dropdown, and edit and delete controls.

`templates/add.html` and `templates/edit.html` are the task forms. The add form creates a new task and the edit form pre-populates all fields from the existing record. Both include the cognitive load range slider, which uses a small inline JS output element to display the selected value in real time.

`templates/login.html` serves both login and registration on the same page using two separate forms pointing to `/login` and `/register` respectively.

`static/styles.css` contains the full styling for the application. It uses a dark theme with CSS custom properties for all colours, Space Mono for body text, and Syne for headings and the logo. Task cards are colour-coded by status and task type using left border accents and badge backgrounds.

`static/app.js` contains a single piece of functionality: an event listener on every status dropdown that fires a `fetch()` POST request to the `/status/<id>` endpoint when the value changes. On success it updates the card's CSS class immediately so the border colour reflects the new status without a page reload. This was a deliberate choice to demonstrate asynchronous JS while keeping the implementation proportionate to the actual need.

## Design Choices

The biggest design decision was what fields to include on a task. Title, due date, and priority flag are mandatory but I decided to replace the priority flag with two more specific concepts. The first is blast radius, which forces the EM to name who or what is blocked if the task slips. The second is cognitive load, scored from one to five, which distinguishes between tasks that are time consuming and tasks that are truly mentally demanding. An EM can have a light calendar day and still carry an enormous cognitive load. Separating those two dimensions produces a more honest picture of capacity than any priority label can imo.

The choice to use SQLite rather than a more powerful database, and vanilla JS rather than a frontend framework, was intentionally lazy. The CS50 SQL library makes queries readable and a single fetch call in plain JS so its an adequate tool. 

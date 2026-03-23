🚀Project Overview

This application allows users to register items and track their movement automatically using RFID technology.

When an RFID tag attached to an item is scanned by the reader antennas, the system determines whether the item is entering (IN) or leaving (OUT) based on which antenna detects the tag.

The system records all transactions in a database and allows users to generate custom reports based on date ranges.

⚙️ How the System Works\

Register items in the web application.

Attach an RFID tag to each registered item.

The Zebra FX9600 reader continuously scans RFID tags.

If the tag is detected by:

Antenna 1 → Item marked as IN

Antenna 2 → Item marked as OUT

The system automatically logs the movement in the database.

✨ Features
Item Registration

Register new items in the system

Assign RFID tags to items

Automated RFID Tracking

Fully automated RFID tag reading

No manual entry required

Antenna-Based Movement Detection

Antenna 1 → IN

Antenna 2 → OUT

Report Generation

Generate reports based on custom date ranges

View item movement history

User Management

Create multiple users

Role-based access control

Automated Workflow

Once items are registered, tracking works automatically through RFID reads

🛠 Technologies Used
Frontend

HTML – Structure of the user interface

CSS – Styling of the application

Tailwind CSS – Responsive UI design framework

JavaScript – Client-side logic and interaction

Backend

Python – Backend programming language

FastAPI – High-performance web framework used to create APIs and handle server-side operations

Database

Microsoft SQL Server (MS SQL) – Stores item details, RFID logs, user data, and reports

Automation

Batch Scripts (.bat) – Used to automate server startup and execution

Hardware Integration

Zebra FX9600 Fixed RFID Reader

RFID Tags

RFID Antennas

📊 Reports

The system allows users to generate reports by selecting:

Start Date

End Date

Reports show:

Item entry records

Item exit records

Complete item movement history

🖥 Hardware Requirements

Zebra FX9600 Fixed RFID Reader

RFID Antennas

RFID Tags

📦 Use Cases

This system can be used for:

Warehouse inventory management

Asset tracking systems

Library automation

Equipment tracking

Entry/Exit monitoring of tagged items

👤 User Roles

The system supports role-based access, allowing administrators to:

Create new users

Assign roles

Manage system access

📁 Project Structure (Example)
project-folder
│
├── static
│   ├── css
│   ├── js
│    ── html 
├── templates
│   ├── login.html
│   ├── dashboard.html
│   ├── register-item.html
│
├── backend
│   ├── main.py
│   ├── database.py
│   ├── models.py
│
├── scripts
│   ├── start_server.bat
│
└── README.md

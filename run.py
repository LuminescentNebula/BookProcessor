from app import create_app

app = create_app({"BOOTSTRAP_ADMIN": True, "START_FOLDER_WATCHER": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

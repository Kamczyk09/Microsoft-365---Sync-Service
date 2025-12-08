
source .venv/bin/activate

# 2) Install dependencies
pip install -r requirements.txt

# 3) (Optional) change base state dir, default is ./state
# Example to use /opt/thalamind:
export THALAMIND_BASE_DIR=/opt/thalamind
sudo mkdir -p /opt/thalamind
sudo chown "$(id -u):$(id -g)" /opt/thalamind

# 4) Verify import smoke test
python3 -c "import thalamind_backend; print('IMPORT_OK')"

# 5) Run device-code auth for a user (follow printed instructions in terminal)
python3 -m thalamind_backend.cli auth alice

# 6) Start sync worker with initial sync (downloads OneDrive root children)
python3 -m thalamind_backend.cli start alice --initial

# 7) To run the worker long-running (foreground), use start without --initial
python3 -m thalamind_backend.cli start alice

# 8) Stop (if supported by your CLI manager) — Ctrl+C for foreground runs
#    or use CLI stop (if implemented):
python3 -m thalamind_backend.cli stop alice

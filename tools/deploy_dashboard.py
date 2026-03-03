import os
import subprocess
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def deploy_to_github():
    """
    Automates pushing the JSON data file to GitHub.
    Assumes the user has set up a remote pointing to their Streamlit Cloud repository.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    json_path = os.path.join(project_root, 'data', 'weekly_gap_watchlist.json')
    
    if not os.path.exists(json_path):
        logging.error(f"Cannot deploy: {json_path} does not exist. Run scanner first.")
        return
        
    logging.info("Preparing to push weekly watchlist to GitHub...")
    
    # Git commands
    # 🟢 [P0 Fix] git add -A 确保代码变更（如 web_viewer.py）也随数据一起推送到 GitHub
    commands = [
        ["git", "add", "-A"],
        ["git", "commit", "-m", f"Auto-update Brooks-AI Radar Data - {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
        ["git", "push"]
    ]
    
    for cmd in commands:
        try:
            result = subprocess.run(cmd, cwd=project_root, check=True, capture_output=True, text=True)
            if result.stdout:
                logging.info(result.stdout.strip())
        except subprocess.CalledProcessError as e:
            if "nothing to commit" in e.stdout.lower() or "nothing to commit" in e.stderr.lower():
                logging.info("No changes to JSON data detected. Skipping commit.")
                break
            logging.error(f"Git command failed: {' '.join(cmd)}")
            logging.error(f"Error output: {e.stderr}")
            break
            
    logging.info("Deployment script finished.")

if __name__ == "__main__":
    deploy_to_github()

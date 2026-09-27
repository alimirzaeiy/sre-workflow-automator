import sys
import os

# Ensure the repository root is on sys.path so that 'deploy_automation' package can be imported in tests
repo_root = os.path.abspath(os.path.dirname(__file__))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

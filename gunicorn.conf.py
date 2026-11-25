# Read automatically by gunicorn ("gunicorn app:app"). ONE process (background checker + backup live inside it),
# many threads so slow requests (Gmail/Paytm/DNS) never block other users.
import os
bind = "0.0.0.0:" + os.environ.get("PORT", "5000")
workers = 1
worker_class = "gthread"
threads = 32
timeout = 90
graceful_timeout = 20
keepalive = 5

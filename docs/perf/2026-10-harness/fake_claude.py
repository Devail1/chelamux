# Stand-in for a claude pane: cmdline matches `pgrep -f claude`, prints like an agent.
import time
import random

i = 0
while True:
    i += 1
    print(f"⏺ step {i}: " + "lorem ipsum " * random.randint(1, 8), flush=True)
    time.sleep(random.uniform(1.0, 4.0))

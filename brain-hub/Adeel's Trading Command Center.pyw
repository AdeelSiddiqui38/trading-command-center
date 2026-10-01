# Double-click to start the trading bots in the background and open the Command Center.
# (Plain Python launcher - no batch/VBS/PowerShell, so antivirus leaves it alone.)
import os, runpy, sys
HUB = os.path.join(os.path.expanduser("~"), "Documents", "BRAIN", "trading-hub", "hub.py")
sys.argv = [HUB]
os.chdir(os.path.dirname(HUB))
runpy.run_path(HUB, run_name="__main__")

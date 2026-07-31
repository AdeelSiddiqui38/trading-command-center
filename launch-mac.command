#!/bin/bash
# Double-click launcher for macOS. Opens the dashboard in your default browser.
DIR="$(cd "$(dirname "$0")" && pwd)"
open "$DIR/index.html"

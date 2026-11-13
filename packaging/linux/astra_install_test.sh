#!/bin/sh
# Install the deb and run the headless smoke test inside Astra.
set -u

echo "=== dpkg -i ==="
echo 'it~support' | sudo -S -p '' dpkg -i /home/helpdesk/pdf-multisign.deb 2>&1 | tail -12
echo "=== installed files ==="
ls -l /usr/bin/pdf-multisign
head -3 /usr/bin/pdf-multisign
echo "=== smoke ==="
export PYTHONPATH=/opt/pdf-multisign:/opt/pdf-multisign/vendor
export QT_QPA_PLATFORM=offscreen
python3 /home/helpdesk/astra_smoke.py /home/helpdesk/astra-ui.png 2>&1 | tail -30
echo "=== screenshot ==="
ls -l /home/helpdesk/astra-ui.png 2>&1

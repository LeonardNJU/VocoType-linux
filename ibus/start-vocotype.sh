#!/bin/bash
# IBus VoCoType Engine startup script

export PYTHONPATH=/usr/share/vocotype:$PYTHONPATH
exec python3 -m ibus.main "$@"

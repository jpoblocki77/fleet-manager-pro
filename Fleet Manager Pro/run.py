"""Fleet Manager Pro launcher.

    run.py                        serve on http://127.0.0.1:8760 (this PC only)
    run.py --lan                  also accept other PCs on your network, encrypted (https://THIS-PC:8760)
    run.py --lan --insecure-http  LAN without encryption (only on a network you fully trust)
    run.py --port 9000            choose a port
    run.py --no-browser           don't open the browser
    run.py agent --server https://HOST:8760 --token TOKEN --pin FINGERPRINT
                                  stream this PC's game telemetry to another Fleet Manager Pro
                                  (token and fingerprint are shown on your profile page there)
"""
import os
import sys

if __name__ == "__main__":
    if "__compiled__" in globals():                     # Nuitka .exe: sys.executable is a phantom python.exe
        os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))
    elif getattr(sys, "frozen", False) or os.path.basename(sys.executable).lower() == "fleet manager pro.exe":
        os.chdir(os.path.dirname(os.path.abspath(sys.executable)))
    from fmpro.launcher import main
    main()

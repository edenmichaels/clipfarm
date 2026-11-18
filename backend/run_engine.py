"""PyInstaller entry point for the ClipFarm engine sidecar."""
import multiprocessing

from clipfarm.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()

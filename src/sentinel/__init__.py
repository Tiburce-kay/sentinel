"""Sentinel — authentification faciale locale, chiffrée et résistante à l'usurpation."""

import os as _os

# OpenCV affiche des avertissements internes sans intérêt pour l'utilisateur.
_os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

__version__ = "0.2.2"

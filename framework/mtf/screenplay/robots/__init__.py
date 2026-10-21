"""Screen Robots: one class per screen, owning that screen's test IDs and interaction details.

`screens.py` holds behaviour that is identical on both platforms (the IDs are shared). `android.py`
and `ios.py` override only what genuinely differs, each difference documented by the app team in
its platform-differences table (back navigation, system dialogs, alerts, keyboard handling).
"""

from mtf.screenplay.robots.factory import Robots, robots_for

__all__ = ["Robots", "robots_for"]

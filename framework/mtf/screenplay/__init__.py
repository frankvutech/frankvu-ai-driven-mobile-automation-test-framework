"""Screenplay: actors with abilities perform tasks and interactions and ask questions.

Business scenarios are expressed once, in terms of user intent (``login``, ``add_product_to_cart``).
Tasks delegate screen details to Screen Robots, which delegate device work to a `UiDriver` port, so
the same task runs on Android (UIAutomator) and iOS (XCUITest) and API tasks run without a device.
"""

# WoundAILite photo and depth binding

A new camera capture has a raw photo orientation, an upright resized JPEG, and a depth map already transformed using the original photo orientation. Before assigning the JPEG dimensions to that depth map, the Lite capture screen checks the recorded orientation against the raw photo and validates the existing RGB dimensions. An unbound pair (both dimensions zero) may be bound once. A partially specified or conflicting size cannot be replaced by the current photo's dimensions. On rejection the photo stays available on screen, with no depth estimate or research recognition request from that capture.

A completed personal recognition is a separate journal. Before drawing its saved polygons, the record page checks the completed receipt, original JPEG SHA-256, normalized JPEG orientation and dimensions. It shows an explanation if the original image is missing or cannot be matched. Checking the preview does not create work, change the receipt or change any saved measurement.

Simulator regression coverage in `LiteRawDepthPacketTests` includes prior dimensions at the same aspect ratio, a partial dimension pair, conflicting EXIF tags, all eight matching orientations, and a portrait capture resized to the 2,048-pixel working space. `LiteServiceOperationTests.testSavedPreviewRequiresTheExactCompletedPhotoEvenAtIdenticalDimensions` uses two distinct, encrypted local images with the same size and the service fixture's signed result path.

These checks establish coordinate-space and file identity. Actual RGB-to-depth optical registration and measurement accuracy still require physical target validation.

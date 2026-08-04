# Visual lane

Text-only mode:

- Route explicit dependencies such as `如下圖`, `根據所附影像`, or `依下表` to visual review.
- Do not route contextual mentions such as `胸部 X 光片顯示無異常`.
- Do not claim crop, placement, or content correctness without actual pixels.

Vision mode:

- Confirm that the request actually includes pixels.
- Inspect required asset count, crop completeness, placement, and table readability.
- Emit advisory only; never write human visual state or modify question text.

"""Select the multipart frame encoding from an explicit format or Accept."""

import re

from capture_node.core.cameras.contracts import PreviewFormat
from capture_node.web.problems import ApiProblem


def negotiate_preview(format: str | None, accept: str | None) -> PreviewFormat:
    if format is not None:
        try:
            return PreviewFormat(format)
        except ValueError as exc:
            raise ApiProblem(
                422,
                "invalid_preview_format",
                "Invalid preview format",
                "Preview format must be png or jpeg.",
            ) from exc
    if accept is None:
        return PreviewFormat.PNG

    ranges: dict[str, float] = {}
    for item in accept.split(","):
        media_type, *parameters = item.lower().strip().split(";")
        quality = 1.0
        supported_parameters = True
        for parameter in parameters:
            name, _, value = parameter.strip().partition("=")
            name, value = name.strip(), value.strip()
            if name == "q":
                if not re.fullmatch(r"(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)", value):
                    raise ApiProblem(
                        400,
                        "invalid_preview_accept",
                        "Invalid Accept header",
                        "Accept quality must be between 0 and 1 with at most three decimals.",
                    )
                quality = float(value)
            else:
                # Encodings have no media-type parameters to match.
                supported_parameters = False
        if not supported_parameters:
            continue
        media_type = media_type.strip()
        ranges[media_type] = max(ranges.get(media_type, 0.0), quality)

    # A container-only request leaves the frame encoding at its default.
    if not any(key.startswith("image/") or key == "*/*" for key in ranges):
        if ranges.get("multipart/x-mixed-replace", 0) > 0:
            return PreviewFormat.PNG

    def quality(format: PreviewFormat) -> float:
        for media_type in (f"image/{format.value}", "image/*", "*/*"):
            if media_type in ranges:
                return ranges[media_type]
        return 0.0

    selected = max(PreviewFormat, key=quality)  # PNG wins equal preferences.
    if quality(selected) == 0:
        raise ApiProblem(
            406,
            "preview_format_not_acceptable",
            "Preview format not acceptable",
            "Preview supports image/png and image/jpeg frames.",
        )
    return selected

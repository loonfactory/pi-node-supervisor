export default function ImagePolicyEditor({ model }) {
  const formatEditor = (format) => {
    const options = model.imageDraft[format];
    return (
      <div className="image-format-editor" key={format}>
        <label className="image-metadata-control">
          <span>{format.toUpperCase()}</span>
          <input
            type="checkbox"
            checked={Boolean(options)}
            disabled={Boolean(options) && Object.keys(model.imageDraft).length === 1}
            onChange={(event) =>
              model.setImageFormatEnabled(format, event.target.checked)
            }
          />
        </label>
        {options && (
          <>
            <label className="image-quality-control">
              <span>{format === "png" ? "Compression" : "Quality"}</span>
              <div>
                <input
                  type="range"
                  min={format === "png" ? "0" : "1"}
                  max={format === "png" ? "9" : "100"}
                  step="1"
                  value={format === "png" ? options.compression : options.quality}
                  onChange={(event) => model.updateImageFormat(
                    format,
                    format === "png" ? "compression" : "quality",
                    Number(event.target.value),
                  )}
                />
                <input
                  type="number"
                  min={format === "png" ? "0" : "1"}
                  max={format === "png" ? "9" : "100"}
                  step="1"
                  value={format === "png" ? options.compression : options.quality}
                  onChange={(event) => model.updateImageFormat(
                    format,
                    format === "png" ? "compression" : "quality",
                    Number(event.target.value),
                  )}
                />
              </div>
            </label>
            <label className="image-metadata-control">
              <span>Embed metadata</span>
              <input
                type="checkbox"
                checked={options.embedMetadata}
                onChange={(event) =>
                  model.updateImageFormat(format, "embedMetadata", event.target.checked)
                }
              />
            </label>
          </>
        )}
      </div>
    );
  };

  return (
    <div className="field-group image-policy-editor">
      <span className="field-label">Image formats</span>
      {formatEditor("png")}
      {formatEditor("jpeg")}
      {model.config?.camera?.type === "picamera2" && (
        <div className="image-format-editor">
          <label className="image-metadata-control">
            <span>DNG</span>
            <input
              type="checkbox"
              checked={Boolean(model.imageDraft.dng)}
              disabled={
                Boolean(model.imageDraft.dng) &&
                Object.keys(model.imageDraft).length === 1
              }
              onChange={(event) =>
                model.setImageFormatEnabled("dng", event.target.checked)
              }
            />
          </label>
          {model.imageDraft.dng && (
            <label className="image-metadata-control">
              <span>Embed metadata</span>
              <input
                type="checkbox"
                checked={model.imageDraft.dng.embedMetadata}
                onChange={(event) =>
                  model.updateImageFormat("dng", "embedMetadata", event.target.checked)
                }
              />
            </label>
          )}
        </div>
      )}
    </div>
  );
}

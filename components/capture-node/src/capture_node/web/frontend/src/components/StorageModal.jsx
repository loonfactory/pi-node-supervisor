import {
  ChevronLeft,
  ChevronRight,
  Download,
  Image as ImageIcon,
  X,
} from "lucide-react";

import { formatBytes, storageFileUrl } from "../lib/presentation";

export default function StorageModal({ model }) {
  if (!model.open) return null;

  return (
    <div className="modal-backdrop" role="presentation">
      <section className="storage-modal" role="dialog" aria-modal="true">
        <div className="section-heading compact">
          <div>
            <span className="eyebrow">Storage</span>
            <h2>Saved captures</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            title="Close saved captures"
            aria-label="Close saved captures"
            onClick={model.close}
          >
            <X size={18} />
          </button>
        </div>
        <div className="storage-toolbar">
          <select
            value={model.target}
            onChange={(event) => model.selectTarget(event.target.value)}
          >
            {model.targets.map((target) => (
              <option key={target.name} value={target.name}>
                {target.name}
              </option>
            ))}
          </select>
          <span>{model.meta.total} files</span>
        </div>
        {model.files.length === 0 ? (
          <div className="empty-state">No captures stored</div>
        ) : (
          <div className="storage-grid">
            {model.files.map((file) => {
              const url = storageFileUrl(model.target, file.name);
              const previewable = /\.(jpe?g|png)$/i.test(file.name);
              return (
                <article className="stored-file" key={file.name}>
                  <a href={url} target="_blank" rel="noreferrer">
                    {previewable ? (
                      <img src={url} alt={file.name} loading="lazy" />
                    ) : (
                      <ImageIcon size={30} />
                    )}
                  </a>
                  <div>
                    <strong title={file.name}>{file.name}</strong>
                    <span>{formatBytes(file.size)}</span>
                  </div>
                  <a
                    className="small-icon-button"
                    href={url}
                    download={file.name}
                    title="Download capture"
                    aria-label={`Download ${file.name}`}
                  >
                    <Download size={16} />
                  </a>
                </article>
              );
            })}
          </div>
        )}
        <div className="pagination">
          <button
            className="icon-button"
            type="button"
            title="Previous storage page"
            aria-label="Previous storage page"
            disabled={model.page <= 1}
            onClick={() => model.changePage(Math.max(1, model.page - 1))}
          >
            <ChevronLeft size={18} />
          </button>
          <span>
            Page {model.page} of {model.meta.pages}
          </span>
          <button
            className="icon-button"
            type="button"
            title="Next storage page"
            aria-label="Next storage page"
            disabled={model.page >= model.meta.pages}
            onClick={() =>
              model.changePage(Math.min(model.meta.pages, model.page + 1))
            }
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </section>
    </div>
  );
}

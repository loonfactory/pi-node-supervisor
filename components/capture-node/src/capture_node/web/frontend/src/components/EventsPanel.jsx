import { ChevronLeft, ChevronRight } from "lucide-react";

import EventRow from "./EventRow";

export default function EventsPanel({ model }) {
  return (
    <section className="events-section">
      <div className="section-heading compact">
        <div>
          <span className="eyebrow">Current process</span>
          <h2>Recent activity</h2>
        </div>
        <span className="event-count">{model.meta.total}</span>
      </div>
      <div className="event-table">
        {model.items.length === 0 ? (
          <div className="empty-state">No events recorded</div>
        ) : (
          model.items.map((item, index) => (
            <EventRow key={`${item.timestamp}-${index}`} item={item} />
          ))
        )}
      </div>
      <div className="pagination">
        <button
          className="icon-button"
          type="button"
          title="Previous activity page"
          aria-label="Previous activity page"
          disabled={model.page <= 1}
          onClick={model.previous}
        >
          <ChevronLeft size={18} />
        </button>
        <span>
          Page {model.page} of {model.meta.pages}
        </span>
        <button
          className="icon-button"
          type="button"
          title="Next activity page"
          aria-label="Next activity page"
          disabled={model.page >= model.meta.pages}
          onClick={model.next}
        >
          <ChevronRight size={18} />
        </button>
      </div>
    </section>
  );
}

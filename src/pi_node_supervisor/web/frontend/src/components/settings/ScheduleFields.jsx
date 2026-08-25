import { useState } from "react";

import { generateTimes } from "../../utils.js";

export function TimeList({ values, onChange, label }) {
  return (
    <div className="daily-time-list">
      {values.length ? (
        values.map((value, index) => (
          <div className="daily-time-row" key={`${index}-${value}`}>
            <input
              type="time"
              step="1"
              value={value}
              aria-label={`${label} ${index + 1}`}
              onChange={(event) =>
                onChange(
                  values.map((item, itemIndex) =>
                    itemIndex === index ? event.target.value : item,
                  ),
                )
              }
            />
            <button
              type="button"
              className="text-button danger-text"
              onClick={() =>
                onChange(values.filter((_, itemIndex) => itemIndex !== index))
              }
            >
              삭제
            </button>
          </div>
        ))
      ) : (
        <div className="friendly-empty">
          <strong>등록된 시각이 없습니다.</strong>
          <span>‘시각 추가’를 눌러 시각을 입력하세요.</span>
        </div>
      )}
    </div>
  );
}

export function RangeGenerator({ initial, values, onChange, label }) {
  const [ranges, setRanges] = useState([{ ...initial }]);
  const update = (index, key, value) =>
    setRanges((items) =>
      items.map((item, itemIndex) =>
        itemIndex === index ? { ...item, [key]: value } : item,
      ),
    );
  const generate = () =>
    onChange(
      [
        ...new Set(ranges.flatMap((range) => generateTimes(range, label))),
      ].sort(),
    );
  return (
    <>
      <div className="subsection-heading">
        <div>
          <strong>{label} 시간 블록</strong>
          <span>시작·종료와 분 단위 간격을 지정합니다.</span>
        </div>
        <button
          type="button"
          className="ghost"
          onClick={() => setRanges((items) => [...items, { ...initial }])}
        >
          + 블록 추가
        </button>
      </div>
      <div className="capture-range-list">
        {ranges.map((range, index) => (
          <div className="capture-range-block" key={index}>
            <div className="capture-range-heading">
              <span>블록 {index + 1}</span>
              <button
                type="button"
                className="text-button danger-text"
                disabled={ranges.length === 1}
                onClick={() =>
                  setRanges((items) =>
                    items.filter((_, itemIndex) => itemIndex !== index),
                  )
                }
              >
                삭제
              </button>
            </div>
            <div className="capture-range-grid">
              <label className="field">
                <input
                  type="time"
                  value={range.start}
                  onChange={(event) =>
                    update(index, "start", event.target.value)
                  }
                />
                <span>시작</span>
              </label>
              <label className="field">
                <input
                  type="number"
                  min="1"
                  max="1440"
                  value={range.intervalMinutes}
                  onChange={(event) =>
                    update(index, "intervalMinutes", Number(event.target.value))
                  }
                />
                <span>간격(분)</span>
              </label>
              <label className="field">
                <input
                  type="time"
                  value={range.end}
                  onChange={(event) => update(index, "end", event.target.value)}
                />
                <span>종료</span>
              </label>
            </div>
          </div>
        ))}
      </div>
      <button
        type="button"
        className="ghost generate-times-button"
        onClick={generate}
      >
        {label} 시각 생성
      </button>
      <div className="subsection-heading generated-times-heading">
        <div>
          <strong>저장될 시각</strong>
          <span>생성 후 직접 조정할 수 있습니다.</span>
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className="text-button danger-text"
            onClick={() => onChange([])}
          >
            모두 지우기
          </button>
          <button
            type="button"
            className="ghost"
            onClick={() => onChange([...values, "09:00:00"])}
          >
            + 시각 추가
          </button>
        </div>
      </div>
      <TimeList values={values} onChange={onChange} label={label} />
    </>
  );
}

export default function Metric({ icon: Icon, label, value }) {
  return (
    <div className="metric">
      <Icon size={17} aria-hidden="true" />
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

/**
 * A row of mutually exclusive toggle buttons (aria-pressed), e.g. chart ranges 1Y / 3Y / ALL.
 *
 * @param {object} props
 * @param {string} props.label                         accessible group name
 * @param {{value: string, label: string, title?: string}[]} props.options
 * @param {string} props.value
 * @param {(value: string) => void} props.onChange
 */
export default function Segmented({ label, options, value, onChange }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className="segmented-button"
          aria-pressed={option.value === value}
          title={option.title}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

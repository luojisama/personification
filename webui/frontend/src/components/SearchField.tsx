import { Icon } from "./Icon";

export function SearchField({ value, onChange, placeholder = "搜索" }: { value: string; onChange: (value: string) => void; placeholder?: string }) {
  return (
    <label className="search-field">
      <span className="sr-only">{placeholder}</span>
      <Icon name="search" />
      <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} type="search" />
    </label>
  );
}

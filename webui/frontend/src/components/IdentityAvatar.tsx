import { useState } from "react";

export function IdentityAvatar({ src, label, size = "normal", square = false }: { src: string | null | undefined; label: string; size?: "small" | "normal" | "large"; square?: boolean }) {
  const [failed, setFailed] = useState(false);
  const fallback = Array.from(label.trim())[0] ?? "P/F";
  return (
    <span className={`identity-avatar identity-avatar-${size}${square ? " identity-avatar-square" : ""}`} aria-hidden="true">
      {src && !failed ? (
        <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />
      ) : (
        <span>{fallback}</span>
      )}
    </span>
  );
}

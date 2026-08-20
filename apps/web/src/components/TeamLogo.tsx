import { useState } from "react";

const EXTENSIONS = ["svg", "png", "jpg", "jpeg"];

/** Equivalente de `show_team_logo`, app.py:159-165: prueba extensiones en orden, cae al 🏀. */
export function TeamLogo({ slug, size = 48 }: { slug: string; size?: number }) {
  const [attempt, setAttempt] = useState(0);

  if (attempt >= EXTENSIONS.length) {
    return (
      <div style={{ fontSize: size, lineHeight: 1 }} aria-label={`Escudo de ${slug}`}>
        🏀
      </div>
    );
  }

  return (
    <img
      src={`/logos/${slug}.${EXTENSIONS[attempt]}`}
      alt={`Escudo de ${slug}`}
      width={size}
      height={size}
      style={{ objectFit: "contain" }}
      onError={() => setAttempt((a) => a + 1)}
    />
  );
}

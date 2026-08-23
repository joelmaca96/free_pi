import { useState } from "react";

const EXTENSIONS = ["svg", "png", "jpg", "jpeg"];

/**
 * Mapeo `team_id` (contrato nuevo, p.ej. `bas`) → slug de logo (assets/logos/,
 * p.ej. `vitoria`). Los assets siguen nombrados por el slug histórico; el
 * contrato nuevo identifica los equipos por `team_id`.
 */
const TEAM_ID_TO_LOGO_SLUG: Record<string, string> = {
  bas: "vitoria",
  rm: "real-madrid",
  fcb: "barcelona",
  bay: "bayern-muenchen",
  baxi: "manresa",
  jb: "joventut",
  val: "valencia",
  gc: "gran-canaria",
  uni: "unicaja-malaga",
};

/** Equivalente de `show_team_logo`, app.py:159-165: prueba extensiones en orden, cae al 🏀. */
export function TeamLogo({ teamId, size = 48 }: { teamId: string; size?: number }) {
  const [attempt, setAttempt] = useState(0);
  const slug = TEAM_ID_TO_LOGO_SLUG[teamId] ?? teamId;

  if (attempt >= EXTENSIONS.length) {
    return (
      <div style={{ fontSize: size, lineHeight: 1 }} aria-label={`Escudo de ${teamId}`}>
        🏀
      </div>
    );
  }

  return (
    <img
      src={`/logos/${slug}.${EXTENSIONS[attempt]}`}
      alt={`Escudo de ${teamId}`}
      width={size}
      height={size}
      style={{ objectFit: "contain" }}
      onError={() => setAttempt((a) => a + 1)}
    />
  );
}

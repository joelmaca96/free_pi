# ACB API — Endpoints del partido (matchId=104465, Casademont Zaragoza vs Baskonia)
# Fuente: live.acb.com (captura de red real del frontend)

## HEADERS (comunes a todos los endpoints)
x-apikey: 0dd94928-6f57-4c08-a3bd-b1b2f092976e
referer: https://live.acb.com/
origin: https://live.acb.com
user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0
accept: */*

## BASE URL
https://api2.acb.com/api/matchdata

## ENDPOINTS

### Pestaña Resumen
1. GET /api/matchdata/Menu/competition-data
2. GET /api/matchdata/Menu/matchlist?matchId=104465&format=round
3. GET /api/matchdata/MatchHeader/match-header?matchId=104465
4. GET /api/matchdata/Overview/lineup?matchId=104465
5. GET /api/matchdata/Overview/lead-tracker?matchId=104465
6. GET /api/matchdata/Overview/match-leaders?matchId=104465
7. GET /api/matchdata/Overview/match-team-comparison?matchId=104465
8. GET /api/matchdata/MatchShots/match-shots?matchId=104465

### Pestaña Estadísticas
9. GET /api/matchdata/Result/boxscores?matchId=104465

### Pestaña Jugadas
10. GET /api/matchdata/PlayByPlay/play-by-play?matchId=104465

### Pestaña Avanzado
11. GET /api/matchdata/AdvancedStats/match-advanced-stats?matchId=104465
12. GET /api/matchdata/AdvancedStats/player-advanced-stats?matchId=104465&playerLicense=30002444

### Pestaña Crónica
(sin llamadas API adicionales)

## OTROS (acb.com, no live.acb.com)
13. GET https://api2.acb.com/api/seasondata/Competition/matches?competitionId=1&isRoundSelected=true
    (calendario/partidos de la competición)

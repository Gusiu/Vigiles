# CLAUDE.md

Projekt: **Vigiles** - zaliczenie warsztatu z Integracji systemów informatycznych. Pełna specyfikacja: `PLAN.md`. Przeczytaj ją przed pierwszą zmianą w kodzie.

## Zasady pracy

- Kamienie milowe po kolei (`PLAN.md`, sekcja 13). Kamień kończy się: zielone testy, aktualna dokumentacja, commit, tag (`v3.0`, `v4.0`, `v5.0`).
- Po każdym kamieniu zatrzymaj się, pokaż, co powstało, i zapytaj przed następnym.
- Decyzji z sekcji 2 nie zmieniaj bez pytania. Zakres pilnuj według sekcji 17. Pułapki: sekcja 18.
- Każdy wzorzec i każdy scenariusz awarii ma test. Spójność asynchroniczna: `eventually()`, nigdy `sleep()`.
- Środowisko: na starcie jeden `docker pull hello-world`. Wynik 403 -> tryb lokalny (sekcja 14), bez ponawiania i bez obchodzenia proxy.
- Sekrety tylko w `.env` (w `.gitignore`). W repo `.env.example` i szablony.
- Identyfikatory w kodzie po angielsku. UI i dokumentacja po polsku. W polskich dokumentach zwykły łącznik `-`, bez półpauz i pauz.
- Przed każdym tagiem przejdź macierz zgodności (sekcja 3): każdy wiersz musi mieć działający dowód.

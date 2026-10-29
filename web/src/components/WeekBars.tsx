import type { Dashboard } from "../api";

/* Seven days, seven bars. Minutes listened, bucketed by the day the listener
 * actually lived through.
 *
 * One series, so there is no legend and no colour coding: the accent carries
 * all seven, and only the busiest day is labelled. A number over every bar is
 * noise when the shape is the point. */

type Day = Dashboard["stats"]["week"][number];

export function WeekBars({ week }: { week: Day[] }) {
  const peak = Math.max(...week.map((day) => day.minutes));

  if (peak === 0) {
    return (
      <p className="muted">
        Nothing played in the last seven days. This fills in as you listen.
      </p>
    );
  }

  return (
    <div className="week" role="img" aria-label={`Minutes listened per day: ${week.map((d) => `${d.weekday} ${d.minutes}`).join(", ")}`}>
      {week.map((day, index) => {
        const isPeak = day.minutes === peak;
        const today = index === week.length - 1;
        return (
          <div className="week-day" key={day.day}>
            <span className="week-value" aria-hidden="true">
              {isPeak ? `${day.minutes}m` : " "}
            </span>
            <span className="week-track">
              <span
                className={isPeak ? "week-fill week-fill-peak" : "week-fill"}
                style={{ height: `${Math.max((day.minutes / peak) * 100, 2)}%` }}
              />
            </span>
            <span className={today ? "week-label week-label-today" : "week-label"}>
              {today ? "today" : day.weekday}
            </span>
          </div>
        );
      })}
    </div>
  );
}

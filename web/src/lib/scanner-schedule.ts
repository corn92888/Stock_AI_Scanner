export type IntradaySchedule = {
  slot: string;
  cron: string;
};

export const INTRADAY_SCHEDULES: IntradaySchedule[] = [
  { slot: "09:00", cron: "0 1 * * 1-5" },
  { slot: "09:30", cron: "30 1 * * 1-5" },
  { slot: "10:00", cron: "0 2 * * 1-5" },
  { slot: "10:30", cron: "30 2 * * 1-5" },
  { slot: "11:00", cron: "0 3 * * 1-5" },
  { slot: "11:30", cron: "30 3 * * 1-5" },
  { slot: "12:00", cron: "0 4 * * 1-5" },
  { slot: "12:30", cron: "30 4 * * 1-5" },
  { slot: "13:00", cron: "0 5 * * 1-5" },
  { slot: "13:30", cron: "30 5 * * 1-5" },
];

export function currentIntradaySchedule(now = new Date()) {
  const taipei = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  const weekday = taipei.getUTCDay();
  if (weekday === 0 || weekday === 6) return null;

  const hour = taipei.getUTCHours();
  const minute = taipei.getUTCMinutes() < 30 ? 0 : 30;
  const slot = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  return INTRADAY_SCHEDULES.find((schedule) => schedule.slot === slot) ?? null;
}

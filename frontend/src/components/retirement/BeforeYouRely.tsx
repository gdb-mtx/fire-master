// The two caveats that matter most, stated where the numbers start.

export default function BeforeYouRely() {
  return (
    <section className="rt-caveats" aria-labelledby="rt-caveats-h">
      <h2 id="rt-caveats-h">Before you rely on this</h2>
      <p>
        <b>This assumes you've stopped working.</b> Paychecks and new savings aren't in this projection
        yet. Starting to earn? Add it as an income source of type Other, with start and end dates, and it
        will count.
      </p>
      <p>
        <b>Taxes on withdrawals aren't counted yet.</b> IRA, 72(t) and RRIF money is spent in full, so
        real results will be lower.
      </p>
    </section>
  );
}

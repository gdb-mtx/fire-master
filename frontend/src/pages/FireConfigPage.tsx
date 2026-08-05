import { useState, useEffect } from "react";
import { Link } from "react-router";
import {
  useFireConfig,
  useUpdateFireConfig,
  useIncomeSources,
  useCreateIncomeSource,
  useDeleteIncomeSource,
  useScenarios,
  useCreateScenario,
  useDeleteScenario,
  useActivateScenario,
} from "../api/queries";
import { formatCurrency } from "../utils/formatting";
import type { FireScenario } from "../types/fire";

export default function FireConfigPage() {
  const { data: config, isLoading } = useFireConfig();
  const updateConfig = useUpdateFireConfig();
  const { data: incomeSources } = useIncomeSources();
  const createIncome = useCreateIncomeSource();
  const deleteIncome = useDeleteIncomeSource();

  const [form, setForm] = useState({
    date_of_birth: "",
    target_retirement_age: "",
    life_expectancy: "90",
    fire_variant: "regular",
    safe_withdrawal_rate: "4.0",
    expected_annual_return: "7.0",
    expected_inflation_rate: "3.0",
    target_annual_spending: "",
    social_security_monthly: "",
    social_security_start_age: "67",
    pension_monthly: "",
    pension_start_age: "",
    healthcare_monthly_cost: "",
    medicare_start_age: "65",
    rmd_start_age: "73",
    state_tax_rate: "",
    federal_marginal_rate: "",
    notes: "",
    // Tax engine fields (stored in custom_assumptions.tax)
    filing_status: "single",
    household_size: "1",
    cost_basis_pct: "60",
    state_of_residence: "",
    // Projection assumptions (stored in custom_assumptions.projection)
    // ALL RATES ARE REAL (after inflation, in today's dollars)
    surplus_investment_rate: "4",
    cash_reserve_months: "12",
    cash_savings_rate_early: "1",
    cash_savings_rate_late: "0",
    cash_savings_cutover_month: "60",
    re_appreciation_rate: "1",
    primary_property_purchase_price: "",
    primary_property_agent_fee_pct: "6",
    primary_property_mortgage_pi: "",
    ss_early_reduction: "70",
    ss_claim_age: "62",
    spending_phase_slow: "85",
    spending_phase_floor: "75",
    spending_phase_slow_age: "70",
    spending_phase_floor_age: "80",
    ira_b_draw_threshold_months: "12",
    rental_occupancy_rate: "100",
  });

  const [incomeForm, setIncomeForm] = useState({
    name: "",
    income_type: "salary",
    annual_amount: "",
    end_date: "",
    growth_rate: "",
  });

  const [saveStatus, setSaveStatus] = useState<"idle" | "saved">("idle");
  const [incomeError, setIncomeError] = useState("");
  const [lastConfigVersion, setLastConfigVersion] = useState<string>("");

  useEffect(() => {
    const version = config?.updated_at ?? "";
    if (config && version !== lastConfigVersion) {
      const ca = config.custom_assumptions as Record<string, unknown> | undefined;
      const tax = ca?.tax as Record<string, unknown> | undefined;
      const proj = ca?.projection as Record<string, unknown> | undefined;
      setForm({
        date_of_birth: config.date_of_birth || "",
        target_retirement_age: config.target_retirement_age?.toString() || "",
        life_expectancy: config.life_expectancy.toString(),
        fire_variant: config.fire_variant,
        safe_withdrawal_rate: config.safe_withdrawal_rate.toString(),
        expected_annual_return: config.expected_annual_return.toString(),
        expected_inflation_rate: config.expected_inflation_rate.toString(),
        target_annual_spending: config.target_annual_spending ? (config.target_annual_spending / 100).toString() : "",
        social_security_monthly: config.social_security_monthly ? (config.social_security_monthly / 100).toString() : "",
        social_security_start_age: config.social_security_start_age.toString(),
        pension_monthly: config.pension_monthly ? (config.pension_monthly / 100).toString() : "",
        pension_start_age: config.pension_start_age?.toString() || "",
        healthcare_monthly_cost: config.healthcare_monthly_cost ? (config.healthcare_monthly_cost / 100).toString() : "",
        medicare_start_age: config.medicare_start_age.toString(),
        rmd_start_age: config.rmd_start_age.toString(),
        state_tax_rate: config.state_tax_rate?.toString() || "",
        federal_marginal_rate: config.federal_marginal_rate?.toString() || "",
        notes: config.notes || "",
        filing_status: (tax?.filing_status as string) || "single",
        household_size: (tax?.household_size as number)?.toString() || "1",
        cost_basis_pct: (tax?.cost_basis_pct as number) != null ? ((tax!.cost_basis_pct as number) * 100).toString() : "60",
        state_of_residence: (tax?.state as string) || "",
        // Projection assumptions — read from saved config or use defaults
        surplus_investment_rate: proj?.surplus_investment_rate != null ? ((proj.surplus_investment_rate as number) * 100).toString() : "4",
        cash_reserve_months: (proj?.cash_reserve_months as number)?.toString() || "12",
        cash_savings_rate_early: proj?.cash_savings_rate_early != null ? ((proj.cash_savings_rate_early as number) * 100).toString() : "1",
        cash_savings_rate_late: proj?.cash_savings_rate_late != null ? ((proj.cash_savings_rate_late as number) * 100).toString() : "0",
        cash_savings_cutover_month: (proj?.cash_savings_cutover_month as number)?.toString() || "60",
        re_appreciation_rate: proj?.re_appreciation_rate != null ? ((proj.re_appreciation_rate as number) * 100).toString() : "1",
        primary_property_purchase_price: (proj?.primary_property_purchase_price as number)?.toString() || "",
        primary_property_agent_fee_pct: proj?.primary_property_agent_fee_pct != null ? ((proj.primary_property_agent_fee_pct as number) * 100).toString() : "6",
        primary_property_mortgage_pi: (proj?.primary_property_mortgage_pi as number)?.toString() || "",
        ss_early_reduction: proj?.ss_early_reduction != null ? ((proj.ss_early_reduction as number) * 100).toString() : "70",
        ss_claim_age: (proj?.ss_claim_age as number)?.toString() || "62",
        spending_phase_slow: proj?.spending_phase_slow != null ? ((proj.spending_phase_slow as number) * 100).toString() : "85",
        spending_phase_floor: proj?.spending_phase_floor != null ? ((proj.spending_phase_floor as number) * 100).toString() : "75",
        spending_phase_slow_age: (proj?.spending_phase_slow_age as number)?.toString() || "70",
        spending_phase_floor_age: (proj?.spending_phase_floor_age as number)?.toString() || "80",
        ira_b_draw_threshold_months: (proj?.ira_b_draw_threshold_months as number)?.toString() || "12",
        rental_occupancy_rate: ca?.rental_occupancy_rate != null ? ((ca.rental_occupancy_rate as number) * 100).toString() : "100",
      });
      setLastConfigVersion(version);
    }
  }, [config, lastConfigVersion]);

  const handleSave = () => {
    const data: Record<string, unknown> = {};

    if (form.date_of_birth) data.date_of_birth = form.date_of_birth;
    if (form.target_retirement_age) data.target_retirement_age = parseInt(form.target_retirement_age);
    if (form.life_expectancy) data.life_expectancy = parseInt(form.life_expectancy);
    data.fire_variant = form.fire_variant;
    if (form.safe_withdrawal_rate) data.safe_withdrawal_rate = parseFloat(form.safe_withdrawal_rate);
    if (form.expected_annual_return) data.expected_annual_return = parseFloat(form.expected_annual_return);
    if (form.expected_inflation_rate) data.expected_inflation_rate = parseFloat(form.expected_inflation_rate);
    if (form.target_annual_spending) data.target_annual_spending = Math.round(parseFloat(form.target_annual_spending) * 100);
    if (form.social_security_monthly) data.social_security_monthly = Math.round(parseFloat(form.social_security_monthly) * 100);
    if (form.social_security_start_age) data.social_security_start_age = parseInt(form.social_security_start_age);
    if (form.pension_monthly) data.pension_monthly = Math.round(parseFloat(form.pension_monthly) * 100);
    if (form.pension_start_age) data.pension_start_age = parseInt(form.pension_start_age);
    if (form.healthcare_monthly_cost) data.healthcare_monthly_cost = Math.round(parseFloat(form.healthcare_monthly_cost) * 100);
    if (form.medicare_start_age) data.medicare_start_age = parseInt(form.medicare_start_age);
    if (form.rmd_start_age) data.rmd_start_age = parseInt(form.rmd_start_age);
    if (form.state_tax_rate) data.state_tax_rate = parseFloat(form.state_tax_rate);
    if (form.federal_marginal_rate) data.federal_marginal_rate = parseFloat(form.federal_marginal_rate);
    data.notes = form.notes || null;

    // Parse a float that allows zero (unlike ||, which treats 0 as falsy)
    const pf = (v: string, fallback: number) => { const n = parseFloat(v); return isNaN(n) ? fallback : n; };

    // custom_assumptions is sent as an RFC 7386 merge patch (fire-master#9):
    // the backend merges it into the existing object, so only form-managed
    // keys appear here — unmanaged keys (sell_event_label_match, sepp, ...)
    // survive without resending them. Clearing a field means sending null;
    // omitting a key would leave the old value in place.
    data.custom_assumptions = {
      tax: {
        filing_status: form.filing_status,
        household_size: parseInt(form.household_size) || 1,
        cost_basis_pct: pf(form.cost_basis_pct, 60) / 100,
        state: form.state_of_residence || null,
        state_tax_rate: form.state_tax_rate ? parseFloat(form.state_tax_rate) : null,
      },
      projection: {
        surplus_investment_rate: pf(form.surplus_investment_rate, 4) / 100,
        cash_reserve_months: parseInt(form.cash_reserve_months) || 12,
        cash_savings_rate_early: pf(form.cash_savings_rate_early, 1) / 100,
        cash_savings_rate_late: pf(form.cash_savings_rate_late, 0) / 100,
        cash_savings_cutover_month: parseInt(form.cash_savings_cutover_month) || 60,
        re_appreciation_rate: pf(form.re_appreciation_rate, 1) / 100,
        primary_property_purchase_price: form.primary_property_purchase_price
          ? parseInt(form.primary_property_purchase_price)
          : null,
        primary_property_agent_fee_pct: pf(form.primary_property_agent_fee_pct, 6) / 100,
        primary_property_mortgage_pi: form.primary_property_mortgage_pi
          ? parseInt(form.primary_property_mortgage_pi)
          : null,
        ss_early_reduction: pf(form.ss_early_reduction, 70) / 100,
        ss_claim_age: parseInt(form.ss_claim_age) || 62,
        spending_phase_slow: pf(form.spending_phase_slow, 85) / 100,
        spending_phase_floor: pf(form.spending_phase_floor, 75) / 100,
        spending_phase_slow_age: parseInt(form.spending_phase_slow_age) || 70,
        spending_phase_floor_age: parseInt(form.spending_phase_floor_age) || 80,
        ira_b_draw_threshold_months: parseInt(form.ira_b_draw_threshold_months) || 12,
      },
      rental_occupancy_rate: pf(form.rental_occupancy_rate, 100) / 100,
    };

    updateConfig.mutate(data, {
      onSuccess: () => {
        setSaveStatus("saved");
        setTimeout(() => setSaveStatus("idle"), 2000);
      },
    });
  };

  const handleAddIncome = () => {
    if (!incomeForm.name || !incomeForm.annual_amount) {
      setIncomeError("Name and annual amount are required");
      return;
    }
    setIncomeError("");
    createIncome.mutate({
      name: incomeForm.name,
      income_type: incomeForm.income_type,
      annual_amount: Math.round(parseFloat(incomeForm.annual_amount) * 100),
      frequency: "monthly",
      end_date: incomeForm.end_date || undefined,
      growth_rate: incomeForm.growth_rate ? parseFloat(incomeForm.growth_rate) : undefined,
    } as any, {
      onSuccess: () => {
        setIncomeForm({ name: "", income_type: "salary", annual_amount: "", end_date: "", growth_rate: "" });
        setIncomeError("");
      },
      onError: (err: Error) => {
        setIncomeError(err.message);
      },
    });
  };

  const { data: activeScenarios } = useScenarios();
  const activeScenario = activeScenarios?.find((s) => s.is_active);

  if (isLoading || !config) {
    return (
      <>
        <div className="flex items-center justify-center h-96 text-[var(--text-secondary)]">
          Loading...
        </div>
      </>
    );
  }

  const inputCls = "w-full bg-[var(--bg-secondary)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] placeholder:opacity-50 focus:outline-none focus:border-[var(--blue)] transition-colors";
  const labelCls = "block text-xs uppercase tracking-wider text-[var(--text-secondary)] mb-1.5";

  return (
    <>
      <div className="space-y-6 max-w-4xl">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold text-[var(--text-primary)] tracking-tight">
              FIRE Configuration
            </h2>
            <p className="text-sm text-[var(--text-secondary)] mt-1">
              Base assumptions powering all projections. Scenario overrides are applied on top.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {saveStatus === "saved" && (
              <span className="text-xs text-[var(--green)]">Saved</span>
            )}
            <button
              onClick={handleSave}
              disabled={updateConfig.isPending}
              className="px-4 py-1.5 text-xs font-medium bg-[var(--blue)] text-white rounded hover:brightness-110 transition-all disabled:opacity-50"
            >
              {updateConfig.isPending ? "Saving..." : "Save Configuration"}
            </button>
          </div>
        </div>

        {/* Active Scenario Banner */}
        {activeScenario && <ActiveScenarioDetails scenario={activeScenario} />}

        {/* Personal */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
          <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-4">Personal</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <label className={labelCls}>Date of Birth</label>
              <input type="date" value={form.date_of_birth} onChange={(e) => setForm(f => ({ ...f, date_of_birth: e.target.value }))} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Target Retirement Age</label>
              <input type="number" value={form.target_retirement_age} onChange={(e) => setForm(f => ({ ...f, target_retirement_age: e.target.value }))} placeholder="55" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Life Expectancy</label>
              <input type="number" value={form.life_expectancy} onChange={(e) => setForm(f => ({ ...f, life_expectancy: e.target.value }))} placeholder="90" className={inputCls} />
            </div>
          </div>
        </div>

        {/* Core Assumptions */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
          <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-4">Core Assumptions</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className={labelCls}>FIRE Variant</label>
              <select value={form.fire_variant} onChange={(e) => setForm(f => ({ ...f, fire_variant: e.target.value }))} className={inputCls}>
                <option value="lean">Lean</option>
                <option value="regular">Regular</option>
                <option value="fat">Fat</option>
                <option value="coast">Coast</option>
              </select>
            </div>
            <div>
              <label className={labelCls}>Safe Withdrawal Rate %</label>
              <input type="number" step="0.1" value={form.safe_withdrawal_rate} onChange={(e) => setForm(f => ({ ...f, safe_withdrawal_rate: e.target.value }))} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Expected Annual Return %</label>
              <input type="number" step="0.1" value={form.expected_annual_return} onChange={(e) => setForm(f => ({ ...f, expected_annual_return: e.target.value }))} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Expected Inflation %</label>
              <input type="number" step="0.1" value={form.expected_inflation_rate} onChange={(e) => setForm(f => ({ ...f, expected_inflation_rate: e.target.value }))} className={inputCls} />
            </div>
          </div>
          <div className="mt-4">
            <label className={labelCls}>Target Annual Spending ($ — leave blank to use actual trailing 12mo)</label>
            <input type="number" value={form.target_annual_spending} onChange={(e) => setForm(f => ({ ...f, target_annual_spending: e.target.value }))} placeholder="Auto-computed from spending data" className={inputCls} />
          </div>
        </div>

        {/* Social Security + Pension */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
          <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-4">Post-Retirement Income</h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className={labelCls}>Social Security Monthly ($)</label>
              <input type="number" value={form.social_security_monthly} onChange={(e) => setForm(f => ({ ...f, social_security_monthly: e.target.value }))} placeholder="0" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>SS Start Age</label>
              <input type="number" value={form.social_security_start_age} onChange={(e) => setForm(f => ({ ...f, social_security_start_age: e.target.value }))} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Pension Monthly ($)</label>
              <input type="number" value={form.pension_monthly} onChange={(e) => setForm(f => ({ ...f, pension_monthly: e.target.value }))} placeholder="0" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Pension Start Age</label>
              <input type="number" value={form.pension_start_age} onChange={(e) => setForm(f => ({ ...f, pension_start_age: e.target.value }))} placeholder="" className={inputCls} />
            </div>
          </div>
        </div>

        {/* Healthcare */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
          <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-4">Healthcare</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <label className={labelCls}>Healthcare Monthly ($)</label>
              <input type="number" value={form.healthcare_monthly_cost} onChange={(e) => setForm(f => ({ ...f, healthcare_monthly_cost: e.target.value }))} placeholder="ACA cost pre-Medicare" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Medicare Start Age</label>
              <input type="number" value={form.medicare_start_age} onChange={(e) => setForm(f => ({ ...f, medicare_start_age: e.target.value }))} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>RMD Start Age</label>
              <input type="number" value={form.rmd_start_age} onChange={(e) => setForm(f => ({ ...f, rmd_start_age: e.target.value }))} className={inputCls} />
            </div>
          </div>
        </div>

        {/* Tax Planning */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-medium text-[var(--text-secondary)]">Tax Planning</h3>
            <Link to="/tax" className="text-xs text-[var(--blue)] hover:underline transition-colors">
              View Tax Dashboard &rarr;
            </Link>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <label className={labelCls}>Filing Status</label>
              <select value={form.filing_status} onChange={(e) => setForm(f => ({ ...f, filing_status: e.target.value }))} className={inputCls}>
                <option value="single">Single</option>
                <option value="married_filing_jointly">Married Filing Jointly</option>
              </select>
            </div>
            <div>
              <label className={labelCls}>State of Residence</label>
              <input type="text" value={form.state_of_residence} onChange={(e) => setForm(f => ({ ...f, state_of_residence: e.target.value }))} placeholder="CA" maxLength={2} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>State Tax Rate %</label>
              <input type="number" step="0.01" value={form.state_tax_rate} onChange={(e) => setForm(f => ({ ...f, state_tax_rate: e.target.value }))} placeholder="5.0" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Federal Marginal Rate %</label>
              <input type="number" step="0.1" value={form.federal_marginal_rate} onChange={(e) => setForm(f => ({ ...f, federal_marginal_rate: e.target.value }))} placeholder="Auto-computed" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Household Size (ACA)</label>
              <input type="number" value={form.household_size} onChange={(e) => setForm(f => ({ ...f, household_size: e.target.value }))} placeholder="1" min={1} max={10} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Est. Cost Basis %</label>
              <input type="number" step="1" value={form.cost_basis_pct} onChange={(e) => setForm(f => ({ ...f, cost_basis_pct: e.target.value }))} placeholder="60" min={0} max={100} className={inputCls} />
              <span className="text-[10px] text-[var(--text-secondary)] mt-1 block">% of taxable accounts that is cost basis (not taxed on withdrawal)</span>
            </div>
          </div>
        </div>

        {/* Projection Assumptions */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
          <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-1">Projection Assumptions</h3>
          <p className="text-[11px] text-[var(--text-secondary)] mb-4">
            Every number that drives the wealth projection. All rates are <strong>real (after inflation, in today's dollars)</strong>.
            Spending stays flat = constant purchasing power. SS stays flat = COLA offsets inflation.
          </p>

          {/* Investment & Savings */}
          <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2 mt-2">Investment &amp; Savings Returns (Real, After Inflation)</h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className={labelCls}>Surplus Investment % (real)</label>
              <input type="number" step="0.5" value={form.surplus_investment_rate} onChange={(e) => setForm(f => ({ ...f, surplus_investment_rate: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">Real after-tax return on invested surplus (historical ~3-4%)</p>
            </div>
            <div>
              <label className={labelCls}>Cash Reserve (months)</label>
              <input type="number" step="1" value={form.cash_reserve_months} onChange={(e) => setForm(f => ({ ...f, cash_reserve_months: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">Emergency fund. Below = savings rate, above = investment rate</p>
            </div>
            <div>
              <label className={labelCls}>Savings Rate Early % (real)</label>
              <input type="number" step="0.5" value={form.cash_savings_rate_early} onChange={(e) => setForm(f => ({ ...f, cash_savings_rate_early: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">Real rate on cash reserve first N months (~1% = savings 4% - 3% inflation)</p>
            </div>
            <div>
              <label className={labelCls}>Savings Rate Late % (real)</label>
              <input type="number" step="0.5" value={form.cash_savings_rate_late} onChange={(e) => setForm(f => ({ ...f, cash_savings_rate_late: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">Real rate on cash reserve long-term (~0% = savings just tracks inflation)</p>
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 mt-3">
            <div>
              <label className={labelCls}>HYSA Cutover Month</label>
              <input type="number" step="12" value={form.cash_savings_cutover_month} onChange={(e) => setForm(f => ({ ...f, cash_savings_cutover_month: e.target.value }))} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>IRA-B Draw Threshold (mo)</label>
              <input type="number" step="1" value={form.ira_b_draw_threshold_months} onChange={(e) => setForm(f => ({ ...f, ira_b_draw_threshold_months: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">Draw IRA-B when cash &lt; this many months of gap</p>
            </div>
            <div>
              <label className={labelCls}>Rental Occupancy %</label>
              <input type="number" step="5" value={form.rental_occupancy_rate} onChange={(e) => setForm(f => ({ ...f, rental_occupancy_rate: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">Rental income multiplier (70% = 30% vacancy)</p>
            </div>
          </div>

          {/* Primary Property */}
          <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2 mt-5">Primary Property</h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className={labelCls}>RE Appreciation % (real)</label>
              <input type="number" step="0.5" value={form.re_appreciation_rate} onChange={(e) => setForm(f => ({ ...f, re_appreciation_rate: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">Real RE appreciation (~1% above inflation historically)</p>
            </div>
            <div>
              <label className={labelCls}>Primary Property Purchase Price $</label>
              <input type="number" step="1000" value={form.primary_property_purchase_price} onChange={(e) => setForm(f => ({ ...f, primary_property_purchase_price: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">Used to calculate sale proceeds</p>
            </div>
            <div>
              <label className={labelCls}>Agent Fee %</label>
              <input type="number" step="0.5" value={form.primary_property_agent_fee_pct} onChange={(e) => setForm(f => ({ ...f, primary_property_agent_fee_pct: e.target.value }))} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Primary Property Mortgage P&amp;I $</label>
              <input type="number" step="1" value={form.primary_property_mortgage_pi} onChange={(e) => setForm(f => ({ ...f, primary_property_mortgage_pi: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">P&amp;I portion of the all-in monthly cost (removed when mortgage paid off)</p>
            </div>
          </div>

          {/* Social Security & Spending */}
          <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2 mt-5">Social Security &amp; Spending Phases</h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label className={labelCls}>SS Claim Age</label>
              <input type="number" step="1" value={form.ss_claim_age} onChange={(e) => setForm(f => ({ ...f, ss_claim_age: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">62=early (70%), 67=full (100%)</p>
            </div>
            <div>
              <label className={labelCls}>SS Benefit %</label>
              <input type="number" step="1" value={form.ss_early_reduction} onChange={(e) => setForm(f => ({ ...f, ss_early_reduction: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">% of full benefit at claim age</p>
            </div>
            <div>
              <label className={labelCls}>Slow-Go Age</label>
              <input type="number" step="1" value={form.spending_phase_slow_age} onChange={(e) => setForm(f => ({ ...f, spending_phase_slow_age: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">Spending drops to {form.spending_phase_slow}%</p>
            </div>
            <div>
              <label className={labelCls}>No-Go Age</label>
              <input type="number" step="1" value={form.spending_phase_floor_age} onChange={(e) => setForm(f => ({ ...f, spending_phase_floor_age: e.target.value }))} className={inputCls} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">Spending drops to {form.spending_phase_floor}%</p>
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 mt-3">
            <div>
              <label className={labelCls}>Slow-Go Spending %</label>
              <input type="number" step="5" value={form.spending_phase_slow} onChange={(e) => setForm(f => ({ ...f, spending_phase_slow: e.target.value }))} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>No-Go Spending %</label>
              <input type="number" step="5" value={form.spending_phase_floor} onChange={(e) => setForm(f => ({ ...f, spending_phase_floor: e.target.value }))} className={inputCls} />
            </div>
          </div>
        </div>

        {/* Income Sources */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
          <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-4">
            Income Sources
            <span className="ml-2 text-xs font-normal">
              ({incomeSources?.length ?? 0})
            </span>
          </h3>

          {/* Existing sources */}
          {incomeSources && incomeSources.length > 0 && (
            <div className="space-y-2 mb-4">
              {incomeSources.map((src) => (
                <div key={src.id} className="flex items-center justify-between py-2 px-3 rounded bg-[var(--bg-secondary)]">
                  <div>
                    <span className="text-sm text-[var(--text-primary)]">{src.name}</span>
                    <span className="ml-2 text-xs text-[var(--text-secondary)]">{src.income_type}</span>
                    {src.end_date && <span className="ml-2 text-xs text-[var(--text-secondary)]">ends {src.end_date}</span>}
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-mono text-[var(--green)]">{formatCurrency(src.annual_amount)}/yr</span>
                    <button onClick={() => deleteIncome.mutate(src.id)} className="text-xs text-[var(--text-secondary)] hover:text-[var(--red)] transition-colors">&times;</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Add new */}
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-5 gap-3 items-end">
            <div>
              <label className={labelCls}>Name</label>
              <input type="text" value={incomeForm.name} onChange={(e) => setIncomeForm(f => ({ ...f, name: e.target.value }))} placeholder="e.g., Salary" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Type</label>
              <select value={incomeForm.income_type} onChange={(e) => setIncomeForm(f => ({ ...f, income_type: e.target.value }))} className={inputCls}>
                <option value="salary">Salary</option>
                <option value="bonus">Bonus</option>
                <option value="rental">Rental</option>
                <option value="dividend">Dividend</option>
                <option value="side_hustle">Side Hustle</option>
                <option value="other">Other</option>
              </select>
            </div>
            <div>
              <label className={labelCls}>Annual ($)</label>
              <input type="number" value={incomeForm.annual_amount} onChange={(e) => setIncomeForm(f => ({ ...f, annual_amount: e.target.value }))} placeholder="0" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>End Date</label>
              <input type="date" value={incomeForm.end_date} onChange={(e) => setIncomeForm(f => ({ ...f, end_date: e.target.value }))} className={inputCls} />
            </div>
            <button onClick={handleAddIncome} disabled={createIncome.isPending} className="px-3 py-2 text-xs font-medium bg-[var(--bg-secondary)] border border-[var(--border)] rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--blue)] transition-colors disabled:opacity-50">
              Add
            </button>
          </div>
          {incomeError && (
            <p className="text-xs text-[var(--red)] mt-2">{incomeError}</p>
          )}
        </div>

        {/* Notes */}
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
          <h3 className="text-sm font-medium text-[var(--text-secondary)] mb-4">FIRE Plan Notes</h3>
          <textarea
            value={form.notes}
            onChange={(e) => setForm(f => ({ ...f, notes: e.target.value }))}
            placeholder="Overall FIRE plan notes, strategy, key decisions..."
            rows={4}
            className={inputCls + " resize-y"}
          />
        </div>

        {/* Scenarios */}
        <ScenariosSection />
      </div>
    </>
  );
}

function ActiveScenarioDetails({ scenario }: { scenario: FireScenario }) {
  const ca = (scenario.overrides as Record<string, unknown>)?.custom_assumptions as Record<string, unknown> | undefined;
  if (!ca) return null;

  const sauvie = ca.sauvie_sale as Record<string, number | boolean> | undefined;
  const str = ca.str_income as Record<string, number | boolean> | undefined;
  const miami = ca.miami_sale as Record<string, number> | undefined;
  const sepp = ca.sepp as Record<string, number> | undefined;
  const rrsp = ca.rrsp as Record<string, number> | undefined;
  const occ = ca.rental_occupancy_rate as number | undefined;
  const propertySales = (ca.property_sales as Record<string, unknown>[] | undefined) ?? [];

  const fmt = (v: number | boolean | undefined) => (v != null ? Number(v).toLocaleString() : "—");
  const fmtK = (v: number | boolean | undefined) => {
    const n = Number(v ?? 0);
    return n >= 1000 ? `$${(n / 1000).toFixed(0)}K` : `$${n.toLocaleString()}`;
  };

  const Row = ({ label, value, color }: { label: string; value: string; color?: string }) => (
    <div className="flex justify-between">
      <span className="text-[var(--text-secondary)]">{label}</span>
      <span className="font-mono" style={color ? { color: `var(--${color})` } : undefined}>{value}</span>
    </div>
  );

  return (
    <div className="bg-[rgba(0,212,170,0.04)] border border-[var(--green)] rounded-lg p-5">
      <div className="flex items-center gap-2 mb-3">
        <span className="w-2 h-2 rounded-full bg-[var(--green)]" />
        <h3 className="text-sm font-semibold text-[var(--green)]">
          Active Scenario: {scenario.name}
        </h3>
      </div>
      {scenario.description && (
        <p className="text-xs text-[var(--text-secondary)] mb-4">{scenario.description}</p>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Generic property sales — one card per entry, titled from the user's own key */}
        {propertySales.map((s) => (
          <PropertySaleCard key={String(s.key ?? "")} sale={s} />
        ))}

        {sauvie?.enabled && (
          <div className="bg-[var(--bg-card)] rounded p-3 border border-[var(--border)]">
            <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2">Legacy: Income Property Sale</h4>
            <div className="space-y-1 text-xs">
              <Row label="Sale timing" value={`Month ${sauvie.sale_month}`} />
              <Row label="Net proceeds" value={fmtK(sauvie.net_proceeds as number)} color="green" />
              <Row label="Primary mortgage payoff" value={`-${fmtK(sauvie.miami_mortgage_paydown as number)}`} color="red" />
              <Row label="Cash from sale" value={fmtK(Number(sauvie.net_proceeds ?? 0) - Number(sauvie.miami_mortgage_paydown ?? 0) - Number(sauvie.park_city_loan_paydown ?? 0))} color="green" />
              <Row label="Monthly costs eliminated" value={`+$${fmt(sauvie.monthly_cost_saved as number)}/mo`} color="green" />
              <Row label="Mortgage P&I eliminated" value={`+$${fmt((ca?.projection as Record<string, number> | undefined)?.primary_property_mortgage_pi ?? 0)}/mo`} color="green" />
              <Row label="Rental income lost" value={`-$${fmt(sauvie.monthly_income_lost as number)}/mo`} color="red" />
            </div>
            <p className="text-[10px] text-[var(--text-secondary)] mt-2 italic">Source: scenario override custom_assumptions.sauvie_sale (legacy)</p>
          </div>
        )}

        {str?.enabled && (
          <div className="bg-[var(--bg-card)] rounded p-3 border border-[var(--border)]">
            <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2">Legacy: STR Income</h4>
            <div className="space-y-1 text-xs">
              <Row label="Primary STR (annual avg)" value={`+$${fmt(str.miami_monthly_averaged as number)}/mo`} color="green" />
              <Row label="Secondary STR (annual avg)" value={`+$${fmt(str.park_city_monthly_averaged as number)}/mo`} color="green" />
              <Row label="STR starts" value={`Month ${str.start_month}`} />
              <p className="text-[10px] text-[var(--text-secondary)] mt-1">
                Each STR income stream stops when its property sells.
              </p>
            </div>
            <p className="text-[10px] text-[var(--text-secondary)] mt-2 italic">Source: scenario override custom_assumptions.str_income (legacy)</p>
          </div>
        )}

        {miami && (
          <div className="bg-[var(--bg-card)] rounded p-3 border border-[var(--border)]">
            <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2">Legacy: Primary Property Sale</h4>
            <div className="space-y-1 text-xs">
              <Row label="Sale timing" value={`Month ${miami.sale_month}`} />
              <Row label="Current monthly cost" value={`$${fmt(miami.monthly_cost)}/mo`} color="red" />
              <Row label="Post-sale rent" value={`$${fmt(miami.post_sale_rent)}/mo`} />
              {sauvie?.enabled ? (
                <p className="text-[10px] text-[var(--green)] mt-1">
                  Mortgage paid off — proceeds calculated dynamically from
                  purchase price + appreciation - agent fees - cap gains tax.
                </p>
              ) : (
                <Row label="Net proceeds (fixed)" value={fmtK(miami.proceeds)} color="green" />
              )}
            </div>
            <p className="text-[10px] text-[var(--text-secondary)] mt-2 italic">Source: scenario override custom_assumptions.miami_sale (legacy)</p>
          </div>
        )}

        {(sepp || rrsp) && (
          <div className="bg-[var(--bg-card)] rounded p-3 border border-[var(--border)]">
            <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2">Bridge Income</h4>
            <div className="space-y-1 text-xs">
              <Row label="SEPP monthly (IRA-A)" value={`$${fmt(sepp?.sepp_monthly ?? 0)}/mo`} />
              <Row label="IRA-A balance" value={fmtK(sepp?.ira_a_balance ?? 0)} />
              <Row label="IRA-B balance (growth)" value={fmtK(sepp?.ira_b_balance ?? 0)} />
              <Row label="IRA-B growth rate" value={`${((sepp?.ira_growth_rate ?? 0.06) as number) * 100}%`} />
              <Row label="RRIF monthly (net)" value={`$${fmt(rrsp?.monthly_net ?? 0)}/mo`} />
              <Row label="RRIF total available" value={fmtK(rrsp?.total_available ?? 0)} />
              <Row label="SEPP + RRIF starts" value={`Month ${sepp?.sepp_start_month ?? 0}`} />
              {occ != null && <Row label="Rental occupancy" value={`${occ * 100}%`} />}
            </div>
            <p className="text-[10px] text-[var(--text-secondary)] mt-2 italic">Source: scenario override custom_assumptions.sepp + rrsp</p>
          </div>
        )}

        {/* Monthly Expense Breakdown by Phase */}
        <ExpenseBreakdown
          sauvie={sauvie}
          miami={miami}
          projection={ca?.projection as Record<string, number> | undefined}
          mortgageRecast={ca?.mortgage_recast as Record<string, number | boolean> | undefined}
          propertySales={propertySales}
        />
      </div>
    </div>
  );
}

function PropertySaleCard({ sale }: { sale: Record<string, unknown> }) {
  const title = String(sale.key ?? "property")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  const num = (v: unknown) => (v != null && !isNaN(Number(v)) ? Number(v) : undefined);
  const money = (v: unknown) => {
    const n = num(v);
    if (n == null) return "—";
    return n >= 1000 ? `$${(n / 1000).toFixed(0)}K` : `$${n.toLocaleString()}`;
  };

  const Row = ({ label, value, color }: { label: string; value: string; color?: string }) => (
    <div className="flex justify-between">
      <span className="text-[var(--text-secondary)]">{label}</span>
      <span className="font-mono" style={color ? { color: `var(--${color})` } : undefined}>{value}</span>
    </div>
  );

  const monthlyCost = num(sale.monthly_cost);
  const postSaleRent = num(sale.post_sale_rent);
  const monthlyIncome = num(sale.monthly_income);

  return (
    <div className="bg-[var(--bg-card)] rounded p-3 border border-[var(--border)]">
      <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2">{title} Sale</h4>
      <div className="space-y-1 text-xs">
        <Row label="Sale timing" value={`Month ${num(sale.sale_month) ?? 0}`} />
        <Row label="Est. value" value={money(sale.value)} color="green" />
        <Row label="Cost basis" value={money(sale.cost_basis)} />
        {monthlyCost != null && monthlyCost > 0 && (
          <Row label="Carrying cost" value={`-$${monthlyCost.toLocaleString()}/mo`} color="red" />
        )}
        {postSaleRent != null && postSaleRent > 0 && (
          <Row label="Post-sale rent" value={`+$${postSaleRent.toLocaleString()}/mo burn`} />
        )}
        {monthlyIncome != null && monthlyIncome > 0 && (
          <Row label="Rental income (ends at sale)" value={`$${monthlyIncome.toLocaleString()}/mo`} />
        )}
        <Row label="Proceeds routed to" value={String(sale.proceeds_to ?? "taxable")} color="green" />
      </div>
      <p className="text-[10px] text-[var(--text-secondary)] mt-2 italic">Source: scenario override custom_assumptions.property_sales</p>
    </div>
  );
}

function ExpenseBreakdown({
  sauvie,
  miami,
  projection,
  mortgageRecast,
  propertySales,
}: {
  sauvie?: Record<string, number | boolean>;
  miami?: Record<string, number>;
  projection?: Record<string, number>;
  mortgageRecast?: Record<string, number | boolean>;
  propertySales?: Record<string, unknown>[];
}) {
  const { data: config } = useFireConfig();
  // Base budget from config (cents/yr → dollars/mo)
  const baseBurn = config?.target_annual_spending
    ? Math.round(config.target_annual_spending / 12 / 100)
    : 0;
  const healthcareMo = config?.healthcare_monthly_cost
    ? Math.round(config.healthcare_monthly_cost / 100)
    : 0;

  const Row = ({ label, value, color, indent }: { label: string; value: string; color?: string; indent?: boolean }) => (
    <div className={`flex justify-between ${indent ? "ml-3" : ""}`}>
      <span className="text-[var(--text-secondary)]">{label}</span>
      <span className="font-mono" style={color ? { color: `var(--${color})` } : undefined}>{value}</span>
    </div>
  );

  // Generic path: derive burn phases from property_sales, in sale order
  if (propertySales && propertySales.length > 0) {
    const sorted = [...propertySales].sort(
      (a, b) => Number(a.sale_month ?? 0) - Number(b.sale_month ?? 0),
    );
    let inBurn = baseBurn;
    // Carrying costs of held properties that are NOT inside the base budget
    let heldExtra = sorted
      .filter((s) => s.in_base_burn === false)
      .reduce((sum, s) => sum + Number(s.monthly_cost ?? 0), 0);

    const phases: { label: string; detail: string; total: number }[] = [
      { label: "Before sales", detail: "base budget + held carrying costs", total: inBurn + heldExtra + healthcareMo },
    ];
    for (const s of sorted) {
      const cost = Number(s.monthly_cost ?? 0);
      if (s.in_base_burn === false) {
        heldExtra -= cost;
      } else {
        inBurn -= cost;
      }
      inBurn += Number(s.post_sale_rent ?? 0);
      const name = String(s.key ?? "").replace(/_/g, " ");
      phases.push({
        label: `After ${name} (mo ${Number(s.sale_month ?? 0)})`,
        detail: `-$${cost.toLocaleString()}${Number(s.post_sale_rent ?? 0) > 0 ? ` +$${Number(s.post_sale_rent).toLocaleString()} rent` : ""}`,
        total: inBurn + heldExtra + healthcareMo,
      });
    }

    return (
      <div className="bg-[var(--bg-card)] rounded p-3 border border-[var(--border)] md:col-span-2">
        <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2">Monthly Burn by Phase</h4>
        <div
          className="grid gap-4 text-xs"
          style={{ gridTemplateColumns: `repeat(${Math.min(phases.length, 4)}, minmax(0, 1fr))` }}
        >
          {phases.map((ph) => (
            <div key={ph.label} className="space-y-1">
              <div className="text-[10px] font-medium text-[var(--text-secondary)] uppercase mb-1">{ph.label}</div>
              <div className="text-[10px] text-[var(--text-secondary)]">{ph.detail}</div>
              <div className="border-t border-[var(--border)] pt-1 mt-1">
                <Row label="Total" value={`$${ph.total.toLocaleString()}/mo`} color="red" />
              </div>
            </div>
          ))}
        </div>
        <p className="text-[10px] text-[var(--text-secondary)] mt-3 italic">
          Source: fire_config.target_annual_spending + healthcare_monthly_cost (${healthcareMo}/mo, drops at Medicare)
          + custom_assumptions.property_sales. Spending phases apply on top.
        </p>
      </div>
    );
  }

  // Legacy path: single primary/income-property model
  const mortgagePI = Number(projection?.primary_property_mortgage_pi ?? 0);
  const sauvieCost = Number(sauvie?.monthly_cost_saved ?? 0);
  const miamiCost = Number(miami?.monthly_cost ?? 0);
  const postSaleRent = Number(miami?.post_sale_rent ?? 0);
  const hasSauvieSale = !!sauvie?.enabled;

  // Mortgage recast awareness
  const hasRecast = !!mortgageRecast?.enabled;
  const recastNewPI = Number(mortgageRecast?.new_monthly_pi ?? mortgagePI);
  const recastMonth = Number(mortgageRecast?.month ?? 0);
  const piSavings = mortgagePI - recastNewPI;
  // After recast, use the new P&I for all subsequent phase calculations
  const effectivePI = hasRecast ? recastNewPI : mortgagePI;

  // Phase expenses
  const phase0 = baseBurn + healthcareMo; // before any sales (original P&I)
  const phase0recast = hasRecast ? baseBurn - piSavings + healthcareMo : phase0; // after recast
  const phase1 = hasSauvieSale
    ? baseBurn - effectivePI - sauvieCost + healthcareMo  // after income-property sale (mortgage gone + its costs gone)
    : (hasRecast ? phase0recast : phase0); // no income-property sale = same as before-sales phase
  const phase2 = hasSauvieSale
    ? baseBurn - miamiCost + postSaleRent - sauvieCost + healthcareMo // after primary sale too
    : baseBurn - miamiCost + postSaleRent + healthcareMo;
  const phase2noHC = phase2 - healthcareMo; // post-Medicare

  return (
    <div className="bg-[var(--bg-card)] rounded p-3 border border-[var(--border)] md:col-span-2">
      <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-secondary)] mb-2">Monthly Expense Breakdown by Phase</h4>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
        {/* Phase: Before property sales */}
        <div className="space-y-1">
          <div className="text-[10px] font-medium text-[var(--text-secondary)] uppercase mb-1">Before Sales</div>
          <Row label="Base budget" value={`$${baseBurn.toLocaleString()}`} />
          <Row label="Healthcare (pre-Medicare)" value={`+$${healthcareMo}`} color="red" />
          <div className="border-t border-[var(--border)] pt-1 mt-1">
            {hasRecast ? (
              <>
                <Row label={`Months 0–${recastMonth - 1}`} value={`$${phase0.toLocaleString()}/mo`} color="red" />
                <Row label={`After recast (mo ${recastMonth}+)`} value={`$${phase0recast.toLocaleString()}/mo`} color="green" />
                <div className="text-[10px] text-[var(--text-secondary)] italic mt-0.5">
                  Recast saves ${piSavings.toLocaleString()}/mo on P&I
                </div>
              </>
            ) : (
              <Row label="Total" value={`$${phase0.toLocaleString()}/mo`} color="red" />
            )}
          </div>
        </div>

        {/* Phase: after the income property sells, before the primary */}
        <div className="space-y-1">
          <div className="text-[10px] font-medium text-[var(--text-secondary)] uppercase mb-1">
            {hasSauvieSale ? "After Income Property Sale" : "Holding All"}
          </div>
          <Row label="Base budget" value={`$${baseBurn.toLocaleString()}`} />
          {hasSauvieSale && <Row label="Mortgage P&I removed" value={`-$${mortgagePI.toLocaleString()}`} color="green" />}
          {hasSauvieSale && <Row label="Income property costs removed" value={`-$${sauvieCost.toLocaleString()}`} color="green" />}
          <Row label="Healthcare (pre-Medicare)" value={`+$${healthcareMo}`} color="red" />
          <div className="border-t border-[var(--border)] pt-1 mt-1">
            <Row label="Total" value={`$${phase1.toLocaleString()}/mo`} color="red" />
          </div>
        </div>

        {/* Phase: After the primary property sells */}
        <div className="space-y-1">
          <div className="text-[10px] font-medium text-[var(--text-secondary)] uppercase mb-1">After Primary Sale</div>
          <Row label="Primary costs removed" value={`-$${miamiCost.toLocaleString()}`} color="green" />
          <Row label="Rent added" value={`+$${postSaleRent.toLocaleString()}`} />
          {hasSauvieSale && <Row label="Income property costs already gone" value="$0" />}
          <Row label="Healthcare (until Medicare)" value={`+$${healthcareMo}`} color="red" />
          <div className="border-t border-[var(--border)] pt-1 mt-1">
            <Row label="Pre-Medicare" value={`$${phase2.toLocaleString()}/mo`} color="red" />
            <Row label="Post-Medicare" value={`$${phase2noHC.toLocaleString()}/mo`} />
          </div>
        </div>
      </div>
      <p className="text-[10px] text-[var(--text-secondary)] mt-3 italic">
        Source: fire_config.target_annual_spending + healthcare_monthly_cost + scenario property adjustments (legacy keys).
        P&amp;I from custom_assumptions.projection.primary_property_mortgage_pi. Spending phases apply on top.
      </p>
    </div>
  );
}

function ScenariosSection() {
  const { data: scenarios, isLoading } = useScenarios();
  const createScenario = useCreateScenario();
  const deleteScenario = useDeleteScenario();
  const activateScenario = useActivateScenario();

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newOverrides, setNewOverrides] = useState("{}");
  const [jsonError, setJsonError] = useState("");

  const handleCreate = () => {
    try {
      const overrides = JSON.parse(newOverrides);
      setJsonError("");
      createScenario.mutate(
        { name: newName, description: newDesc || undefined, overrides },
        {
          onSuccess: () => {
            setShowCreate(false);
            setNewName("");
            setNewDesc("");
            setNewOverrides("{}");
          },
        },
      );
    } catch {
      setJsonError("Invalid JSON");
    }
  };

  const inputCls =
    "w-full bg-[var(--bg-page)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--blue)] placeholder:text-[var(--text-secondary)]";

  return (
    <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-[var(--text-secondary)]">
          Scenarios
        </h3>
        <button
          onClick={() => setShowCreate(!showCreate)}
          className="px-3 py-1 text-xs bg-[var(--blue)] text-white rounded hover:opacity-90"
        >
          {showCreate ? "Cancel" : "New Scenario"}
        </button>
      </div>

      {showCreate && (
        <div className="mb-4 p-4 bg-[var(--bg-page)] border border-[var(--border)] rounded-lg space-y-3">
          <input
            className={inputCls}
            placeholder="Scenario name (e.g., Sell Rental at Month 24)"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <input
            className={inputCls}
            placeholder="Description (optional)"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
          <div>
            <label className="text-[10px] text-[var(--text-secondary)] uppercase tracking-wider mb-1 block">
              Overrides (JSON)
            </label>
            <textarea
              className={inputCls + " font-mono text-xs resize-y"}
              rows={8}
              value={newOverrides}
              onChange={(e) => { setNewOverrides(e.target.value); setJsonError(""); }}
              placeholder='{"custom_assumptions": {"property_sales": [{"key": "rental_condo", "sale_month": 24, ...}]}}'
            />
            {jsonError && (
              <p className="text-xs text-[var(--red)] mt-1">{jsonError}</p>
            )}
          </div>
          <button
            onClick={handleCreate}
            disabled={!newName.trim() || createScenario.isPending}
            className="px-4 py-2 text-xs bg-[var(--green)] text-[var(--bg-page)] rounded font-medium hover:opacity-90 disabled:opacity-50"
          >
            {createScenario.isPending ? "Creating..." : "Create Scenario"}
          </button>
        </div>
      )}

      {isLoading ? (
        <p className="text-xs text-[var(--text-secondary)]">Loading...</p>
      ) : scenarios && scenarios.length > 0 ? (
        <div className="space-y-2">
          {scenarios.map((s) => (
            <div
              key={s.id}
              className={`flex items-center justify-between p-3 rounded-lg border ${
                s.is_active
                  ? "border-[var(--green)] bg-[rgba(0,212,170,0.04)]"
                  : "border-[var(--border)] bg-[var(--bg-page)]"
              }`}
            >
              <div className="flex-1 min-w-0 mr-3">
                <div className="flex items-center gap-2">
                  <span
                    className="w-2 h-2 rounded-full flex-shrink-0"
                    style={{
                      background: s.is_active ? "var(--green)" : "var(--text-secondary)",
                    }}
                  />
                  <span className="text-sm font-medium truncate">
                    {s.name}
                  </span>
                  {s.is_active && (
                    <span className="text-[10px] text-[var(--green)] uppercase tracking-wider font-medium">
                      Active
                    </span>
                  )}
                </div>
                {s.description && (
                  <p className="text-[11px] text-[var(--text-secondary)] mt-0.5 ml-4 truncate">
                    {s.description}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-1.5 flex-shrink-0">
                {s.is_active ? (
                  <span className="px-2 py-1 text-[10px] text-[var(--green)]">
                    Active
                  </span>
                ) : (
                  <button
                    onClick={() => activateScenario.mutate(s.id)}
                    className="px-2 py-1 text-[10px] border border-[var(--border)] rounded hover:border-[var(--green)] text-[var(--green)] transition-colors"
                  >
                    Activate
                  </button>
                )}
                <button
                  onClick={() => {
                    if (confirm(`Delete "${s.name}"?`)) deleteScenario.mutate(s.id);
                  }}
                  className="px-2 py-1 text-[10px] border border-[var(--border)] rounded hover:border-[var(--red)] text-[var(--red)] transition-colors"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-xs text-[var(--text-secondary)]">
          No scenarios saved. Create one to compare different assumptions.
        </p>
      )}
    </div>
  );
}

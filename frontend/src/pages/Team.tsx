import { useState } from "react";
import { act, time, useApi, useMe } from "../api";
import { RationaleButton } from "./CaseDetail";

export default function Team() {
  const me = useMe();
  const { data, reload } = useApi<any[]>("/users", false);
  const [form, setForm] = useState({ name: "", email: "", role: "staff", temporary_password: "" });
  if (me?.role !== "admin") return <div className="empty">Only admins can manage the team.</div>;

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await act("/users", form, `Added ${form.email} — share the temporary password with them privately`);
    if (r) { setForm({ name: "", email: "", role: "staff", temporary_password: "" }); reload(); }
  };
  const patch = async (email: string, body: any, ok: string) => {
    if (await act(`/users/${encodeURIComponent(email)}`, body, ok, "PATCH")) reload();
  };

  return (
    <>
      <div className="pagehead">
        <div>
          <h1>Team</h1>
          <p className="sub">Only admins and staff can sign in. Staff work cases and approve appeals; admins also manage this list.</p>
        </div>
      </div>
      <div className="grid-2">
        <section className="card flush">
          <table>
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last sign-in</th><th /></tr></thead>
            <tbody>
              {(data ?? []).map((u) => (
                <tr key={u.email}>
                  <td className="strong">{u.name}{u.email === me.email && <span className="pill muted">you</span>}</td>
                  <td className="mono">{u.email}</td>
                  <td>
                    <select value={u.role} disabled={u.email === me.email}
                      onChange={(e) => patch(u.email, { role: e.target.value }, `Role changed to ${e.target.value}`)}>
                      <option value="staff">staff</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td>
                    <span className={`pill ${u.active ? "ok" : "muted"}`}>{u.active ? "active" : "disabled"}</span>
                    {u.must_change_password && u.active && <span className="pill warn">temp password</span>}
                  </td>
                  <td className="mono dim">{u.last_login ? time(u.last_login) : "never"}</td>
                  <td className="row-actions tight">
                    {u.email !== me.email && (
                      <button className="sm" onClick={() => patch(u.email, { active: !u.active }, u.active ? "Access removed" : "Access restored")}>
                        {u.active ? "Disable" : "Enable"}
                      </button>
                    )}
                    <RationaleButton label="Reset password" placeholder="New temporary password (10+)"
                      onSubmit={(pw) => patch(u.email, { temporary_password: pw }, "Temporary password set — user must change it at next sign-in")} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="card">
          <h2>Add a team member</h2>
          <form className="stackform" onSubmit={add}>
            <label>Name<input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label>Work email<input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
            <label>Role
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="staff">Staff — cases, exceptions, appeal approvals</option>
                <option value="admin">Admin — everything, plus team management</option>
              </select>
            </label>
            <label>Temporary password (10+ characters)
              <input type="text" autoComplete="off" required minLength={10} value={form.temporary_password}
                onChange={(e) => setForm({ ...form, temporary_password: e.target.value })} />
            </label>
            <button className="primary">Add user</button>
          </form>
          <p className="note">They must replace the temporary password the first time they sign in. Disabling a user or resetting their password signs them out everywhere.</p>
        </section>
      </div>
    </>
  );
}

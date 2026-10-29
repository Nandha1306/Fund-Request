import { Alert, Button, Form, Input } from 'antd'
import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { toApiError } from '../api/errors'
import { useAuth } from '../auth/AuthProvider'
import { BrandMark } from '../components/AppLayout'

const DEMO_ACCOUNTS = [
  { email: 'admin@demo.local', role: 'Super Admin' },
  { email: 'finance@demo.local', role: 'Finance' },
  { email: 'approver.a@demo.local', role: 'Approver' },
  { email: 'req.a@demo.local', role: 'Requestor' },
]

interface Values {
  email: string
  password: string
}

export function AuthBrand() {
  return (
    <div className="auth-brand">
      <BrandMark />
      <div>
        <div className="brand-name">Fund Desk</div>
        <div className="brand-sub">Project fund requests &amp; liquidation</div>
      </div>
    </div>
  )
}

export default function LoginPage() {
  const { status, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [form] = Form.useForm<Values>()
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  if (status === 'authenticated') return <Navigate to={from} replace />

  const submit = async ({ email, password }: Values) => {
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
      navigate(from, { replace: true })
    } catch (err) {
      const e = toApiError(err)
      setError(e.status === 429 ? 'Too many attempts. Wait a minute and try again.' : e.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <AuthBrand />
        <div className="auth-box">
          <h1 className="auth-title">Sign in</h1>
          <p className="auth-desc">Use the email address your administrator invited.</p>
          {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
          <Form form={form} layout="vertical" requiredMark={false} onFinish={submit} disabled={submitting}>
            <Form.Item name="email" label="Email" rules={[{ required: true, message: 'Enter your email' }]}>
              <Input autoComplete="username" autoFocus placeholder="name@company.com" />
            </Form.Item>
            <Form.Item name="password" label="Password" rules={[{ required: true, message: 'Enter your password' }]}>
              <Input.Password autoComplete="current-password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              Sign in
            </Button>
          </Form>
        </div>
        <div className="auth-foot">Access is by invitation only. Contact your administrator for an account.</div>
        {import.meta.env.DEV && (
          <div className="demo-accounts">
            <div className="demo-accounts-title">Demo accounts (local only) · password Demo@1234</div>
            {DEMO_ACCOUNTS.map((a) => (
              <button key={a.email} type="button" onClick={() => form.setFieldsValue({ email: a.email, password: 'Demo@1234' })}>
                <span>{a.email}</span>
                <span className="muted">{a.role}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

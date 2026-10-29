import { Alert, Button, Form, Input } from 'antd'
import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { fieldErrors, toApiError } from '../api/errors'
import { useAuth } from '../auth/AuthProvider'
import { AuthBrand } from './LoginPage'

interface Values {
  full_name: string
  password: string
  confirm: string
}

const byteLength = (s: string) => new TextEncoder().encode(s).length

export default function AcceptInvitePage() {
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const { acceptInvite, logout, status } = useAuth()
  const navigate = useNavigate()
  const [form] = Form.useForm<Values>()
  const [invalid, setInvalid] = useState(!token)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async ({ full_name, password }: Values) => {
    setError(null)
    setSubmitting(true)
    try {
      if (status === 'authenticated') logout() // the link is for someone else's account
      await acceptInvite(token, full_name, password)
      navigate('/', { replace: true })
    } catch (err) {
      const e = toApiError(err)
      const fields = fieldErrors(e)
      if (e.status === 400 && Object.keys(fields).length === 0) setInvalid(true)
      else if (Object.keys(fields).length) form.setFields(Object.entries(fields).map(([name, msg]) => ({ name: name as keyof Values, errors: [msg] })))
      else setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <AuthBrand />
        <div className="auth-box">
          {invalid ? (
            <>
              <h1 className="auth-title">This invitation can't be used</h1>
              <p className="auth-desc">
                The link is invalid, has already been used, or has expired. Invitation links work once and expire after 72 hours.
                Ask your administrator to send a new one.
              </p>
              <Link to="/login">Go to sign in</Link>
            </>
          ) : (
            <>
              <h1 className="auth-title">Set up your account</h1>
              <p className="auth-desc">You've been invited to Fund Desk. Choose your name and a password to finish.</p>
              {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
              <Form form={form} layout="vertical" requiredMark={false} onFinish={submit} disabled={submitting}>
                <Form.Item name="full_name" label="Full name" rules={[{ required: true, whitespace: true, message: 'Enter your name' }, { max: 255 }]}>
                  <Input autoFocus autoComplete="name" />
                </Form.Item>
                <Form.Item
                  name="password"
                  label="Password"
                  extra="At least 8 characters."
                  rules={[
                    { required: true, message: 'Choose a password' },
                    { validator: (_, v: string) => (!v || (byteLength(v) >= 8 && byteLength(v) <= 72) ? Promise.resolve() : Promise.reject(new Error('Use 8 to 72 characters'))) },
                  ]}
                >
                  <Input.Password autoComplete="new-password" />
                </Form.Item>
                <Form.Item
                  name="confirm"
                  label="Confirm password"
                  dependencies={['password']}
                  rules={[
                    { required: true, message: 'Repeat the password' },
                    ({ getFieldValue }) => ({
                      validator: (_, v: string) => (!v || v === getFieldValue('password') ? Promise.resolve() : Promise.reject(new Error('Passwords do not match'))),
                    }),
                  ]}
                >
                  <Input.Password autoComplete="new-password" />
                </Form.Item>
                <Button type="primary" htmlType="submit" block loading={submitting}>
                  Create account
                </Button>
              </Form>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

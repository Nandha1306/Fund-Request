import { CheckOutlined, CopyOutlined } from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Form, Input, Modal, Select } from 'antd'
import { useEffect, useState } from 'react'

import { usersApi } from '../../api/endpoints'
import { fieldErrors, toApiError } from '../../api/errors'
import type { InviteResult, Role } from '../../api/types'
import { formatDateTime } from '../../lib/format'
import { ROLE_HINT, ROLE_LABEL, ROLE_OPTIONS } from '../../lib/labels'

interface Values {
  email: string
  role: Role
}

export function InviteModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form] = Form.useForm<Values>()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const [result, setResult] = useState<InviteResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (open) {
      setResult(null)
      setError(null)
      setCopied(false)
      form.resetFields()
    }
  }, [open, form])

  const invite = useMutation({
    mutationFn: (v: Values) => usersApi.invite(v.email, v.role),
    onSuccess: (r) => {
      setResult(r)
      queryClient.invalidateQueries({ queryKey: ['invitations'] })
      queryClient.invalidateQueries({ queryKey: ['audit'] })
    },
    onError: (err) => {
      const e = toApiError(err)
      const fields = fieldErrors(e)
      if (fields.email) form.setFields([{ name: 'email', errors: [fields.email] }])
      else if (e.status === 409) form.setFields([{ name: 'email', errors: ['Someone with this email already has an account'] }])
      else setError(e.message)
    },
  })

  const copy = async () => {
    if (!result) return
    await navigator.clipboard.writeText(result.invite_link)
    setCopied(true)
    message.success('Link copied')
  }

  const role = Form.useWatch('role', form)

  return (
    <Modal
      open={open}
      title={result ? 'Invitation ready' : 'Invite a user'}
      onCancel={onClose}
      forceRender // keep the Form mounted so it can be reset before the modal opens
      width={520}
      footer={
        result ? (
          <Button type="primary" onClick={onClose}>Done</Button>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button type="primary" loading={invite.isPending} onClick={() => form.submit()}>Create invitation</Button>
          </>
        )
      }
    >
      {result ? (
        <>
          <p style={{ marginTop: 0 }}>
            Send this link to <strong>{result.email}</strong>. They'll join as <strong>{ROLE_LABEL[result.role]}</strong> once they set a password.
          </p>
          <div className="link-box">
            <Input readOnly value={result.invite_link} onFocus={(e) => e.target.select()} />
            <Button icon={copied ? <CheckOutlined /> : <CopyOutlined />} onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>
          </div>
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 14 }}
            title="Copy the link now"
            description={`For security it is shown only once and can be used once. It expires on ${formatDateTime(result.expires_at)}. Inviting the same email again cancels this link.`}
          />
        </>
      ) : (
        <>
          {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
          <Form form={form} layout="vertical" requiredMark={false} onFinish={(v) => invite.mutate(v)} initialValues={{ role: 'REQUESTOR' }}>
            <Form.Item name="email" label="Work email" rules={[{ required: true, message: 'Enter an email address' }, { type: 'email', message: 'Enter a valid email address' }]}>
              <Input autoFocus placeholder="name@company.com" />
            </Form.Item>
            <Form.Item name="role" label="Role" extra={role ? ROLE_HINT[role] : undefined} style={{ marginBottom: 0 }}>
              <Select options={ROLE_OPTIONS} />
            </Form.Item>
          </Form>
        </>
      )}
    </Modal>
  )
}

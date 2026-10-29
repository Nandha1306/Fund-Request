import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Drawer, Form, Select, Switch } from 'antd'
import { useEffect, useState } from 'react'

import { usersApi, type UserChanges } from '../../api/endpoints'
import { requestRefs, toApiError } from '../../api/errors'
import type { AdminUser, Role } from '../../api/types'
import { useCurrentUser } from '../../auth/AuthProvider'
import { RoleBadge, UserStatusBadge } from '../../components/Badges'
import { formatDate } from '../../lib/format'
import { ROLE_HINT, ROLE_OPTIONS } from '../../lib/labels'

interface Values {
  role: Role
  is_active: boolean
  can_finance_ops: boolean
}

interface Problem {
  message: string
  refs: string[]
}

export function EditUserDrawer({ user, onClose }: { user: AdminUser | null; onClose: () => void }) {
  const me = useCurrentUser()
  const [form] = Form.useForm<Values>()
  const queryClient = useQueryClient()
  const { message, modal } = App.useApp()
  const [problem, setProblem] = useState<Problem | null>(null)
  const isSelf = user?.id === me.id
  const role = Form.useWatch('role', form)

  useEffect(() => {
    if (user) {
      setProblem(null)
      form.setFieldsValue({ role: user.role, is_active: user.is_active, can_finance_ops: user.can_finance_ops })
    }
  }, [user, form])

  const save = useMutation({
    mutationFn: (v: Values) => {
      if (!user) throw new Error('No user selected')
      const changes: UserChanges = {}
      if (v.role !== user.role) changes.role = v.role
      if (v.is_active !== user.is_active) changes.is_active = v.is_active
      const ops = v.role === 'SUPER_ADMIN' ? v.can_finance_ops : false
      if (v.role === 'SUPER_ADMIN' && ops !== user.can_finance_ops) changes.can_finance_ops = ops
      return usersApi.update(user.id, changes)
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      queryClient.invalidateQueries({ queryKey: ['audit'] })
      message.success('User updated')
      onClose()
      if (res.needs_reassignment.length) {
        modal.warning({
          title: 'Requests need a new approver',
          content: `${res.user.full_name} was the approver on ${res.needs_reassignment.join(', ')}. Finance should reassign these requests.`,
        })
      }
    },
    onError: (err) => {
      const e = toApiError(err)
      setProblem({ message: e.message, refs: requestRefs(e) })
    },
  })

  return (
    <Drawer
      open={!!user}
      onClose={onClose}
      size={420}
      title={user?.full_name}
      forceRender // keep the Form mounted so values can be set before the drawer opens
      footer={
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="primary" loading={save.isPending} onClick={() => form.submit()}>Save changes</Button>
        </div>
      }
    >
      {user && (
        <>
          <dl className="kv" style={{ marginBottom: 20 }}>
            <dt>Email</dt>
            <dd>{user.email}</dd>
            <dt>Current role</dt>
            <dd><RoleBadge role={user.role} /></dd>
            <dt>Status</dt>
            <dd><UserStatusBadge active={user.is_active} /></dd>
            <dt>Joined</dt>
            <dd>{formatDate(user.created_at)}</dd>
          </dl>
          {isSelf && (
            <Alert type="info" showIcon style={{ marginBottom: 16 }} title="This is your account" description="You can't change your own role or deactivate yourself. Another Super Admin can." />
          )}
          {problem && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              title={problem.message}
              description={problem.refs.length ? `Requests: ${problem.refs.join(', ')}` : undefined}
            />
          )}
          <Form form={form} layout="vertical" onFinish={(v) => save.mutate(v)} onValuesChange={() => setProblem(null)}>
            <Form.Item name="role" label="Role" extra={role ? ROLE_HINT[role] : undefined}>
              <Select options={ROLE_OPTIONS} disabled={isSelf} />
            </Form.Item>
            <Form.Item name="is_active" label="Account active" valuePropName="checked" extra="Deactivated users can't sign in. Their history is kept.">
              <Switch disabled={isSelf} />
            </Form.Item>
            <Form.Item
              name="can_finance_ops"
              label="Finance operations"
              valuePropName="checked"
              extra="Lets this Super Admin disburse, verify and liquidate. Finance users always have these actions."
              hidden={role !== 'SUPER_ADMIN'}
            >
              <Switch />
            </Form.Item>
          </Form>
        </>
      )}
    </Drawer>
  )
}

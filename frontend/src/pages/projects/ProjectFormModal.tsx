import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Form, Input, InputNumber, Modal, Radio } from 'antd'
import { useEffect, useState } from 'react'

import { projectsApi, type ProjectInput } from '../../api/endpoints'
import { fieldErrors, toApiError } from '../../api/errors'
import type { Project, ProjectStatus } from '../../api/types'
import { groupAmountInput, money, parseAmountInput, toNumber } from '../../lib/format'

interface Values {
  code: string
  name: string
  description?: string
  allocated: string
  status: ProjectStatus
}

interface Props {
  open: boolean
  project?: Project // edit when given, create otherwise
  onClose: () => void
  onSaved?: (project: Project) => void
}

export function ProjectFormModal({ open, project, onClose, onSaved }: Props) {
  const editing = !!project
  const [form] = Form.useForm<Values>()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const [formError, setFormError] = useState<string | null>(null)
  const committed = project ? toNumber(project.allocated) - toNumber(project.available) : 0

  useEffect(() => {
    if (!open) return
    setFormError(null)
    form.resetFields()
    if (project) {
      form.setFieldsValue({ name: project.name, description: project.description ?? '', allocated: project.allocated })
    }
  }, [open, project, form])

  const save = useMutation({
    mutationFn: (v: Values) => {
      const description = v.description?.trim() ? v.description.trim() : null
      if (!project) {
        return projectsApi.create({ code: v.code, name: v.name, description, allocated: String(v.allocated), status: v.status })
      }
      // PATCH only what changed, so the audit log records real changes only.
      const changes: ProjectInput = {}
      if (v.name !== project.name) changes.name = v.name
      if (description !== project.description) changes.description = description
      if (toNumber(v.allocated) !== toNumber(project.allocated)) changes.allocated = String(v.allocated)
      return projectsApi.update(project.id, changes)
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['project', saved.id] })
      queryClient.invalidateQueries({ queryKey: ['audit'] })
      message.success(editing ? 'Project updated' : `Project ${saved.code} created`)
      onSaved?.(saved)
      onClose()
    },
    onError: (err) => {
      const e = toApiError(err)
      const fields = fieldErrors(e)
      if (Object.keys(fields).length) {
        form.setFields(Object.entries(fields).map(([name, msg]) => ({ name: name as keyof Values, errors: [msg] })))
      } else if (e.status === 409) {
        form.setFields([{ name: 'code', errors: ['This code is already used by another project'] }])
      } else {
        setFormError(e.message)
      }
    },
  })

  return (
    <Modal
      open={open}
      title={editing ? `Edit ${project.code}` : 'New project'}
      okText={editing ? 'Save changes' : 'Create project'}
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={save.isPending}
      forceRender // keep the Form mounted so it can be reset before the modal opens
      width={520}
    >
      {formError && <Alert type="error" showIcon title={formError} style={{ marginBottom: 16 }} />}
      <Form form={form} layout="vertical" requiredMark={false} onFinish={(v) => save.mutate(v)} initialValues={{ status: 'ACTIVE' }}>
        {!editing && (
          <Form.Item
            name="code"
            label="Project code"
            extra="3–20 characters: letters, digits and hyphens. Cannot be changed later."
            normalize={(v: string) => v?.toUpperCase()}
            rules={[
              { required: true, message: 'Enter a code' },
              { pattern: /^[A-Z0-9-]{3,20}$/, message: 'Use 3–20 letters, digits or hyphens' },
            ]}
          >
            <Input placeholder="PRJ-004" className="mono" />
          </Form.Item>
        )}
        <Form.Item name="name" label="Name" rules={[{ required: true, whitespace: true, message: 'Enter a name' }, { max: 255 }]}>
          <Input placeholder="e.g. Rural Health Camp" />
        </Form.Item>
        <Form.Item name="description" label={<>Description <span className="subtle" style={{ marginLeft: 4 }}>(optional)</span></>}>
          <Input.TextArea rows={3} placeholder="What the budget is for" />
        </Form.Item>
        <Form.Item
          name="allocated"
          label="Allocated budget"
          extra={editing ? `Already committed: ${money(committed)}. The allocation can't go below this.` : 'The total amount that can be requested against this project.'}
          rules={[
            { required: true, message: 'Enter the allocated budget' },
            {
              validator: (_, v) =>
                v === undefined || v === null || v === '' || (toNumber(v) >= committed && /^\d+(\.\d{1,2})?$/.test(String(v)))
                  ? Promise.resolve()
                  : Promise.reject(new Error(toNumber(v) < committed ? `Must be at least ${money(committed)}` : 'Use at most two decimals')),
            },
          ]}
        >
          <InputNumber<string>
            stringMode
            min="0"
            prefix="₹"
            style={{ width: '100%' }}
            formatter={(v) => groupAmountInput(v)}
            parser={(v) => parseAmountInput(v)}
          />
        </Form.Item>
        {!editing && (
          <Form.Item name="status" label="Status" style={{ marginBottom: 0 }}>
            <Radio.Group
              options={[
                { value: 'ACTIVE', label: 'Active — requestors can raise requests' },
                { value: 'INACTIVE', label: 'Inactive' },
              ]}
            />
          </Form.Item>
        )}
      </Form>
    </Modal>
  )
}

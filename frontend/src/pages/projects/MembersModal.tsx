import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Modal, Select } from 'antd'
import { useEffect, useState } from 'react'

import { projectsApi, usersApi } from '../../api/endpoints'
import { toApiError } from '../../api/errors'
import type { Member, Project } from '../../api/types'

interface Props {
  open: boolean
  project: Project
  members: Member[]
  onClose: () => void
}

/** Replace the member list. Only active requestors can be members. */
export function MembersModal({ open, project, members, onClose }: Props) {
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const [selected, setSelected] = useState<number[]>([])
  const [error, setError] = useState<string | null>(null)
  const requestors = useQuery({ queryKey: ['requestors'], queryFn: usersApi.requestors, enabled: open })

  useEffect(() => {
    if (open) {
      setSelected(members.map((m) => m.user_id))
      setError(null)
    }
  }, [open, members])

  const current = new Set(members.map((m) => m.user_id))
  const added = selected.filter((id) => !current.has(id)).length
  const removed = members.filter((m) => !selected.includes(m.user_id)).length

  const save = useMutation({
    mutationFn: () => projectsApi.setMembers(project.id, selected),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project', project.id] })
      queryClient.invalidateQueries({ queryKey: ['audit'] })
      message.success('Members updated')
      onClose()
    },
    onError: (err) => setError(toApiError(err).message),
  })

  // Keep current members selectable even if they are no longer listed as active requestors.
  const options = [
    ...(requestors.data ?? []).map((u) => ({ value: u.id, label: u.full_name, email: u.email })),
    ...members
      .filter((m) => !(requestors.data ?? []).some((u) => u.id === m.user_id))
      .map((m) => ({ value: m.user_id, label: m.full_name, email: m.email })),
  ]

  return (
    <Modal
      open={open}
      title={`Members of ${project.code}`}
      okText="Save members"
      okButtonProps={{ disabled: added + removed === 0 }}
      onOk={() => save.mutate()}
      onCancel={onClose}
      confirmLoading={save.isPending}
      destroyOnHidden
      width={560}
    >
      <p className="muted" style={{ marginTop: 0 }}>
        Members can raise fund requests against this project while it is active. Removing someone stops new requests; their existing requests stay visible to them.
      </p>
      {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 12 }} />}
      <Select
        mode="multiple"
        style={{ width: '100%' }}
        placeholder="Add requestors"
        loading={requestors.isPending}
        value={selected}
        onChange={setSelected}
        options={options}
        optionFilterProp="label"
        optionRender={(o) => (
          <div>
            <div>{o.data.label}</div>
            <div className="cell-sub">{o.data.email}</div>
          </div>
        )}
        maxTagCount="responsive"
      />
      <div className="cell-sub" style={{ marginTop: 8 }}>
        {added + removed === 0 ? 'No changes yet.' : `${added} to add, ${removed} to remove.`}
      </div>
    </Modal>
  )
}

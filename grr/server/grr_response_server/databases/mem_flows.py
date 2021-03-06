#!/usr/bin/env python
"""The in memory database methods for flow handling."""
import sys

from grr.core.grr_response_core.lib import rdfvalue
from grr.core.grr_response_core.lib import utils


class InMemoryDBFlowMixin(object):
  """InMemoryDB mixin for flow handling."""

  @utils.Synchronized
  def WriteMessageHandlerRequests(self, requests):
    """Writes a list of message handler requests to the database."""
    now = rdfvalue.RDFDatetime.Now()
    for r in requests:
      flow_dict = self.message_handler_requests.setdefault(r.handler_name, {})
      cloned_request = r.Copy()
      cloned_request.timestamp = now
      flow_dict[cloned_request.request_id] = cloned_request

  @utils.Synchronized
  def ReadMessageHandlerRequests(self):
    """Reads all message handler requests from the database."""
    res = []
    leases = self.message_handler_leases
    for requests in self.message_handler_requests.values():
      for r in requests.values():
        res.append(r.Copy())
        existing_lease = leases.get(r.handler_name, {}).get(r.request_id, None)
        res[-1].leased_until = existing_lease

    return sorted(res, key=lambda r: -1 * r.timestamp)

  @utils.Synchronized
  def DeleteMessageHandlerRequests(self, requests):
    """Deletes a list of message handler requests from the database."""

    for r in requests:
      flow_dict = self.message_handler_requests.get(r.handler_name, {})
      if r.request_id in flow_dict:
        del flow_dict[r.request_id]
      flow_dict = self.message_handler_leases.get(r.handler_name, {})
      if r.request_id in flow_dict:
        del flow_dict[r.request_id]

  @utils.Synchronized
  def LeaseMessageHandlerRequests(self, lease_time=None, limit=1000):
    """Leases a number of message handler requests up to the indicated limit."""

    leased_requests = []

    now = rdfvalue.RDFDatetime.Now()
    zero = rdfvalue.RDFDatetime.FromSecondsSinceEpoch(0)
    expiration_time = now + lease_time

    leases = self.message_handler_leases
    for requests in self.message_handler_requests.values():
      for r in requests.values():
        existing_lease = leases.get(r.handler_name, {}).get(r.request_id, zero)
        if existing_lease < now:
          leases.setdefault(r.handler_name, {})[r.request_id] = expiration_time
          r.leased_until = expiration_time
          r.leased_by = utils.ProcessIdString()
          leased_requests.append(r)
          if len(leased_requests) >= limit:
            break

    return leased_requests

  @utils.Synchronized
  def ReadClientMessages(self, client_id):
    """Reads all client messages available for a given client_id."""
    res = []
    for msgs_by_id in self.client_messages.values():
      for orig_msg in msgs_by_id.values():
        if orig_msg.queue.Split()[0] != client_id:
          continue
        msg = orig_msg.Copy()
        current_lease = self.client_message_leases.get(msg.task_id)
        if current_lease:
          msg.leased_until, msg.leased_by = current_lease
        res.append(msg)

    return res

  @utils.Synchronized
  def DeleteClientMessages(self, messages):
    """Deletes a list of client messages from the db."""
    for m in messages:
      client_id = m.queue.Split()[0]
      tasks = self.client_messages.get(client_id)
      if not tasks or m.task_id not in tasks:
        # TODO(amoser): Once new flows are in, reevaluate if we can raise on
        # deletion request for unknown messages.
        continue
      del tasks[m.task_id]
      if m.task_id in self.client_message_leases:
        del self.client_message_leases[m.task_id]

  @utils.Synchronized
  def LeaseClientMessages(self, client_id, lease_time=None, limit=sys.maxsize):
    """Leases available client messages for the client with the given id."""
    leased_messages = []

    now = rdfvalue.RDFDatetime.Now()
    expiration_time = now + lease_time
    process_id_str = utils.ProcessIdString()

    leases = self.client_message_leases
    for msgs_by_id in self.client_messages.values():
      for msg in msgs_by_id.values():
        if msg.queue.Split()[0] != client_id:
          continue

        existing_lease = leases.get(msg.task_id)
        if not existing_lease or existing_lease[0] < now:
          leases[msg.task_id] = (expiration_time, process_id_str)
          msg.leased_until = expiration_time
          msg.leased_by = process_id_str
          leased_messages.append(msg)
          if len(leased_messages) >= limit:
            break

    return leased_messages

  @utils.Synchronized
  def WriteClientMessages(self, messages):
    """Writes messages that should go to the client to the db."""
    for m in messages:
      client_id = m.queue.Split()[0]
      self.client_messages.setdefault(client_id, {})[m.task_id] = m
